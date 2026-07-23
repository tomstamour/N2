#!/usr/bin/env python
"""PrClassifier.py — tag each PR row with a catalyst class via regex rules.

Reads a PRstats TSV, matches the Headline (then the lead of the body as
fallback) against an ordered rule list, and appends two columns:
  FineClass  — ~21 specific classes (FDA_Approval, MA, Regain_Compliance, ...)
  BroadClass — grouped rollup (FDA, Clinical, Commercial, ...)
First matching rule wins; rows matching nothing get Other/Other.

Usage:
  /home/tom/venv/bin/python PrClassifier.py --input PRstats_....tsv [--output out.tsv]

Default output: <input-stem>_Classed.tsv next to the input.
"""

import argparse
import csv
import os
import re
import sys
from collections import Counter

# Body fallback only scans the lead of the article: about-us / safe-harbor
# boilerplate deeper in the text is full of false triggers (designations,
# "forward-looking", law-firm footers...).
BODY_LEAD_CHARS = 1500

# (FineClass, BroadClass, pattern) — priority order: first match wins,
# strongest/most specific catalyst first.
RULES = [
    ("FDA_Approval", "FDA",
     r"fda (approv|clear)|(approv|clear)(al|ance|ed|es)?\s*(by|from)? the (u\.?s\.? )?fda"
     r"|510\(k\)|de novo (approval|clearance)|marketing authorization"
     r"|nda (submission|filing|accept)|bla (submission|filing|accept)"
     r"|emergency use authorization"),
    ("FDA_Designation", "FDA",
     r"orphan drug|fast track|breakthrough (therapy|device)|rare pediatric"
     r"|priority review|rmat|qidp designation|\bind (clearance|accept)|ide approval"),
    ("Clinical_Data", "Clinical",
     r"topline|top-line|interim (data|results|analysis)"
     r"|phase [1-3][ab]?(/[1-3][ab]?)? (data|results|trial|study)"
     r"|clinical (data|results)|preclinical (data|results)|positive (data|results)"
     r"|primary endpoint|efficacy|first patient (dosed|enrolled)|patient dos"
     r"|enrollment (complet|of first)|dose (escalation|optimization)"),
    ("Clinical_Update", "Clinical",
     r"phase [1-3]|clinical trial|clinical study|clinical-stage|dosing|enrollment"
     r"|\bnda\b|\bbla\b|investigational"),
    ("MA", "MA",
     r"\bmerger|acquisition|acquires?\b|acquired\b|to acquire|business combination"
     r"|takeover|buyout|strategic alternatives"
     r"|definitive agreement to (acquire|merge|combine)|going.private"),
    ("Regain_Compliance", "Compliance",
     r"regain(s|ed)? compliance|compliance with (the )?nasdaq|compliance with (the )?nyse"),
    ("Deficiency_Notice", "Compliance",
     r"deficien|delist|non-?compliance|notification (letter )?from (the )?(nasdaq|nyse)"
     r"|minimum bid price requirement|continued listing"),
    ("Reverse_Split", "Compliance",
     r"reverse (stock |share )?split"),
    ("Crypto_Treasury", "Crypto",
     r"bitcoin|ethereum|\bsolana\b|crypto|digital asset|blockchain|staking|\bbtc\b|\beth\b"),
    ("Contract_Award", "Commercial",
     r"contract|purchase order|\baward(ed|s)?\b|task order|\bidiq\b|procurement"
     r"|wins? .{0,30}(order|deal|bid)"),
    ("Partnership", "Commercial",
     r"partnership|collaboration|strategic alliance|teams? up|joint venture"
     r"|\bmou\b|memorandum of understanding|letter of intent"
     r"|distribution agreement|license agreement|licensing agreement|supply agreement"),
    ("Product_Launch", "Commercial",
     r"launch|unveil|introduc|debut|now available|releases? new"
     r"|expands? .{0,30}(line|portfolio|offering)|new product"),
    ("Offering_Financing", "Financing",
     r"offering|private placement|pricing of|registered direct|at-the-market"
     r"|convertible note|warrant|pipe financing|capital raise|credit facility"
     r"|loan agreement|financing"),
    ("Buyback_Dividend", "Financing",
     r"buyback|repurchase|dividend|special cash distribution"),
    ("Patent_IP", "IP",
     r"patent|trademark|intellectual property|\buspto\b"),
    ("Earnings_Results", "Earnings",
     r"(reports?|announces?|releases?) .{0,40}(financial|operating)? ?results"
     r"|(first|second|third|fourth)[- ]quarter .{0,20}results|full[- ]year results"
     r"|fiscal (year|q[1-4]) .{0,20}results|quarterly results"),
    ("Earnings_Date", "Earnings",
     r"to (report|announce|release|host|present|provide) .{0,60}"
     r"(results|earnings|conference call|webcast|update)"
     r"|earnings (call|date|release)"
     r"|(results|earnings) on (monday|tuesday|wednesday|thursday|friday|may|june|july|april)"),
    ("Management_Change", "Corporate",
     r"appoint|names? .{0,40}(as )?(chief|ceo|cfo|coo|cto|cmo|president|chairman|director)"
     r"|(ceo|cfo|leadership) (transition|change)|joins? .{0,30}as|steps? down|resign"
     r"|inducement (grant|award)"),
    ("Conference_Presentation", "Corporate",
     r"to present|presents? at|poster|symposium|\bsummit\b|investor conference"
     r"|fireside chat|annual meeting|to participate|to attend|rings? .{0,20}bell"),
    ("Legal", "Legal",
     r"class action|investigation|law firm|lawsuit|litigation|settlement"
     r"|court (rul|approv)|deadline alert"),
    ("Business_Update", "Corporate",
     r"corporate update|business update|shareholder letter|letter to shareholders"
     r"|operational update|provides? update|milestone"),
]

COMPILED = [(fine, broad, re.compile(pat, re.IGNORECASE)) for fine, broad, pat in RULES]


def classify(text):
    """Return (FineClass, BroadClass) for text; ("Other","Other") if no rule hits."""
    for fine, broad, rx in COMPILED:
        if rx.search(text):
            return fine, broad
    return "Other", "Other"


def classify_row(row):
    """Headline first; body lead as fallback. Returns (fine, broad, used_body)."""
    fine, broad = classify(row.get("Headline") or "")
    if fine != "Other":
        return fine, broad, False
    body = (row.get("body") or "").strip() or (row.get("Body") or "")
    fine, broad = classify(body[:BODY_LEAD_CHARS])
    return fine, broad, fine != "Other"


def main():
    ap = argparse.ArgumentParser(description="Add FineClass/BroadClass columns to a PRstats TSV")
    ap.add_argument("--input", required=True, help="input PRstats TSV")
    ap.add_argument("--output", help="output TSV (default: <input-stem>_Classed.tsv)")
    args = ap.parse_args()

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
        writer = csv.DictWriter(fout, fieldnames=reader.fieldnames + ["FineClass", "BroadClass"],
                                delimiter="\t", quoting=csv.QUOTE_NONE, quotechar=None,
                                escapechar=None, lineterminator="\n")
        writer.writeheader()
        for row in reader:
            fine, broad, used_body = classify_row(row)
            row["FineClass"], row["BroadClass"] = fine, broad
            writer.writerow(row)
            n_rows += 1
            n_body += used_body
            fine_counts[fine] += 1
            broad_counts[broad] += 1

    print(f"{n_rows} rows -> {out_path}  ({n_body} classified via body fallback)")
    print("\nFineClass:")
    order = [f for f, _, _ in RULES] + ["Other"]
    for fine in order:
        if fine_counts[fine]:
            print(f"  {fine:26s} {fine_counts[fine]:5d}")
    print("\nBroadClass:")
    for broad, cnt in broad_counts.most_common():
        print(f"  {broad:12s} {cnt:5d}")


if __name__ == "__main__":
    main()
