#!/usr/bin/env python3
"""
verify_ibkr_borrow.py — regression test for ibkr_borrow_addOn's unit conversion.

IBKR returns FEE_RATE / REBATE_RATE as ANNUALIZED DECIMAL FRACTIONS; the borrow
columns are in percent, so ibkr_borrow_addOn scales every close by 100. If that
factor is ever wrong the columns stay plausible-looking and silently mis-scale
the CTB30/CTB80 squeeze flags by 100x, so it is worth a standing check.

The reference is stocks_universe_2026-07-21.tsv — the last file written while
iBorrowDesk was still answering, i.e. the last independent measurement of these
values the project has. Anchoring the fetch at 20:15 ET on that session should
reproduce its CTB_FeePct.

    /home/tom/venv/bin/python verify_ibkr_borrow.py [--port 4002] [--symbols N]

Passes when >= MIN_AGREEMENT of the compared symbols match within TOLERANCE_PCT.
Exact agreement is not expected: the reference is an intraday iBorrowDesk
snapshot while this is the session's daily bar, so a symbol whose fee moved
during the day differs legitimately (measured: 11 of 12 within 0.1%, ADIL 9%).
"""

import argparse
import logging
import os
import sys

import pandas as pd
from ib_insync import IB, Stock

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

REFERENCE_FILE = os.path.join(_SCRIPT_DIR, "data", "stocks_universe_2026-07-21.tsv")
REFERENCE_ANCHOR = "20260721 20:15:00 US/Eastern"

TOLERANCE_PCT = 1.0     # relative, per symbol
MIN_AGREEMENT = 0.80    # fraction of compared symbols that must agree


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4002,
                        help="4002 = paper GW (default; the live GW starves "
                             "historical queries under the trading stack)")
    parser.add_argument("--clientID", type=int, default=65)
    parser.add_argument("--symbols", type=int, default=12,
                        help="How many symbols from the reference file to check")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("ib_insync").setLevel(logging.CRITICAL)

    if not os.path.exists(REFERENCE_FILE):
        print(f"FAIL: reference file missing: {REFERENCE_FILE}")
        return 2

    df = pd.read_csv(REFERENCE_FILE, sep="\t", dtype=str, keep_default_na=False)
    ref = df[df["CTB_FeePct"] != ""].head(args.symbols)
    if ref.empty:
        print(f"FAIL: {REFERENCE_FILE} has no populated CTB_FeePct to compare.")
        return 2

    import ibkr_borrow_addOn as borrow

    ib = IB()
    ib.connect(args.host, args.port, clientId=args.clientID, timeout=20)
    agreed = compared = 0
    try:
        print(f"{'symbol':<8}{'reference':>12}{'IBKR x100':>12}{'ratio':>9}  verdict")
        print("-" * 52)
        for _, row in ref.iterrows():
            symbol = row["Symbol"]
            contracts = ib.qualifyContracts(Stock(symbol, "SMART", "USD"))
            if not contracts:
                print(f"{symbol:<8}{'—':>12}{'—':>12}{'—':>9}  not qualified")
                continue
            bars = ib.reqHistoricalData(
                contracts[0], endDateTime=REFERENCE_ANCHOR,
                durationStr=borrow._BORROW_DURATION,
                barSizeSetting=borrow._BORROW_BAR_SIZE,
                whatToShow="FEE_RATE", useRTH=True, formatDate=1, timeout=60)
            if not bars:
                print(f"{symbol:<8}{'—':>12}{'—':>12}{'—':>9}  no bars")
                continue

            expected = float(row["CTB_FeePct"])
            got = bars[-1].close * 100.0
            ratio = got / expected if expected else float("nan")
            ok = abs(got - expected) <= expected * TOLERANCE_PCT / 100.0
            compared += 1
            agreed += ok
            print(f"{symbol:<8}{expected:>12.4f}{got:>12.4f}{ratio:>9.3f}  "
                  f"{'ok' if ok else 'DIFFERS'}")
    finally:
        ib.disconnect()

    if not compared:
        print("\nFAIL: nothing could be compared.")
        return 1
    rate = agreed / compared
    print("-" * 52)
    print(f"{agreed}/{compared} within {TOLERANCE_PCT}% ({rate:.0%}); "
          f"threshold {MIN_AGREEMENT:.0%}")
    if rate >= MIN_AGREEMENT:
        print("\nPASS — the x100 fraction-to-percent conversion is intact.\n")
        return 0
    print("\nFAIL — IBKR's borrow-rate units may have changed. Check the scale "
          "factor in ibkr_borrow_addOn.fetch_borrow_async before trusting a "
          "nightly run.\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
