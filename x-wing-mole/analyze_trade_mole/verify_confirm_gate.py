#!/usr/bin/env python3
"""Offline checks of trade-mole-2.6's optional signal confirmation (2026-10-08).

Unit cases drive ``IBKRSurgeApp._confirm_gate`` directly with a hand-made clock;
two end-to-end cases replay real 2.5-era tail recordings through the whole
mole. No ibapi connection is made. Exit code 0 = every check passed.

    python verify_confirm_gate.py [--module trade-mole-2.6.py]
"""
import argparse
import logging
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import replay_mole as R  # noqa: E402

OUT_DIR = os.path.normpath(os.path.join(HERE, "..", "mole-outputs"))
FAILS = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"   [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def make_app(mole, **consts):
    log = logging.getLogger("verify")
    log.addHandler(logging.NullHandler())
    log.propagate = False
    for k, v in consts.items():
        setattr(mole, k, v)
    return mole.IBKRSurgeApp(
        "TEST", baseline_avg_iti=100.0,
        surge_dollar_rate=mole.DEFAULT_SURGE_DOLLAR_RATE,
        surge_max_spread_pct=mole.DEFAULT_SURGE_MAX_SPREAD_PCT,
        surge_min_lift_ratio=mole.DEFAULT_SURGE_MIN_LIFT_RATIO,
        surge_min_bid_drift_bp=mole.DEFAULT_SURGE_MIN_BID_DRIFT_BP,
        surge_min_trades_5s=mole.DEFAULT_SURGE_MIN_TRADES_5S,
        surge_max_price_drop_pct=mole.DEFAULT_SURGE_MAX_PRICE_DROP_PCT,
        baseline_avg_trade_size=300.0, large_trade_mult=mole.DEFAULT_LARGE_TRADE_MULT,
        pretrade_staleness_fire=mole.PRETRADE_STALENESS_FIRE,
        pretrade_staleness_threshold=mole.PRETRADE_STALENESS_THRESHOLD, logger=log)


def unit_cases(mole):
    print("unit cases (_confirm_gate)")
    E_REASON = "$rate_5s=5.0>=3&price_chg=+2.00%>=+1.2%&flow_x_base=12.0>=10"
    PRE_REASON = "bslnITI_over_pretrade_gap=500.00>100"

    # 1. letters
    check("rule letters", all(mole.IBKRSurgeApp._rule_letter(r) == l for r, l in [
        (E_REASON, "E"), (PRE_REASON, "PRE"), ("measured_ITI=0.1<=0.28&x", "D"),
        ("bslnITI_on_last3trades_avrgITI=2000>1550", "C"), ("bigmove_price_chg=+5%>=+4%", "N"),
        ("flowburst_x_base=900>=700", "NF"), ("nonsense", "?")]))

    # 2. hold, then release on the first new trade
    app = make_app(mole, CONFIRM_WINDOW_SEC=1.0, CONFIRM_MIN_TRADES=1,
                   CONFIRM_MIN_DAYVOL_LOTS=None, CONFIRM_RULES=("C", "D", "E", "N", "NF", "A", "B"))
    s, r = app._confirm_gate(True, E_REASON, 100.00, False)
    check("signal is held, not fired", s is False and r.startswith("pending 0/1"), f"{s} {r}")
    s, r = app._confirm_gate(False, "", 100.30, False)
    check("first new trade releases it", s is True and r.startswith(E_REASON) and "confirmed=1trades" in r, f"{s} {r}")
    check("nothing is left pending", app._pending_signal is None)

    # 3. a twin report of one fill does not count
    app = make_app(mole, CONFIRM_WINDOW_SEC=1.0, CONFIRM_MIN_TRADES=1, CONFIRM_MIN_DAYVOL_LOTS=None)
    app._confirm_gate(True, E_REASON, 50.00, False)
    s, _ = app._confirm_gate(False, "", 50.002, True)
    check("twin print is not a new trade", s is False)
    s, _ = app._confirm_gate(False, "", 50.40, False)
    check("a distinct trade after the twin releases", s is True)

    # 4. timeout drops the signal; a signal on the same event starts a new hold
    app = make_app(mole, CONFIRM_WINDOW_SEC=1.0, CONFIRM_MIN_TRADES=1, CONFIRM_MIN_DAYVOL_LOTS=None)
    app._confirm_gate(True, E_REASON, 10.00, False)
    s, r = app._confirm_gate(True, E_REASON, 11.50, False)
    check("late trade does not confirm (window over)", s is False and r.startswith("pending 0/1"), f"{s} {r}")
    check("a fresh hold starts at the new signal", app._pending_signal and app._pending_signal["t"] == 11.50)
    app = make_app(mole, CONFIRM_WINDOW_SEC=1.0, CONFIRM_MIN_TRADES=1, CONFIRM_MIN_DAYVOL_LOTS=None)
    app._confirm_gate(True, E_REASON, 10.00, False)
    s, r = app._confirm_gate(False, "", 12.00, False)
    check("expired hold with no new signal clears and returns the rules' answer",
          s is False and r == "" and app._pending_signal is None, f"{s} {r}")

    # 5. two trades needed
    app = make_app(mole, CONFIRM_WINDOW_SEC=1.0, CONFIRM_MIN_TRADES=2, CONFIRM_MIN_DAYVOL_LOTS=None)
    app._confirm_gate(True, E_REASON, 20.00, False)
    s1, _ = app._confirm_gate(False, "", 20.2, False)
    s2, _ = app._confirm_gate(False, "", 20.5, False)
    check("2 trades: first holds, second releases", (s1, s2) == (False, True), f"{s1} {s2}")

    # 6. other signals are suppressed while one is held
    app = make_app(mole, CONFIRM_WINDOW_SEC=1.0, CONFIRM_MIN_TRADES=2, CONFIRM_MIN_DAYVOL_LOTS=None)
    app._confirm_gate(True, E_REASON, 30.00, False)
    s, r = app._confirm_gate(True, "measured_ITI=0.1<=0.28&x", 30.1, False)
    check("a second signal while held is not raised", s is False and app._pending_signal["reason"] == E_REASON)

    # 7. day-volume leg
    app = make_app(mole, CONFIRM_WINDOW_SEC=1.0, CONFIRM_MIN_TRADES=1, CONFIRM_MIN_DAYVOL_LOTS=5.0)
    app._true_vol_log.append((40.0, 1000.0))
    app._confirm_gate(True, E_REASON, 40.0, False)
    app._true_vol_log.append((40.2, 1002.0))
    s, _ = app._confirm_gate(False, "", 40.2, False)
    check("day volume +2 lots < 5: still held", s is False)
    app._true_vol_log.append((40.4, 1006.0))
    s, _ = app._confirm_gate(False, "", 40.4, False)
    check("day volume +6 lots >= 5: released", s is True)

    # 8. a rule outside CONFIRM_RULES is passed through at once
    app = make_app(mole, CONFIRM_WINDOW_SEC=1.0, CONFIRM_MIN_TRADES=1, CONFIRM_MIN_DAYVOL_LOTS=None,
                   CONFIRM_RULES=("D",))
    s, r = app._confirm_gate(True, E_REASON, 50.0, False)
    check("rule E not listed: fires at once", s is True and r == E_REASON and app._pending_signal is None)
    mole.CONFIRM_RULES = ("B", "C", "D", "E", "N", "NF", "A")


def tail_cases(mole):
    print("end-to-end (real tail recordings through the whole mole)")
    log = logging.getLogger("verify2")
    log.addHandler(logging.NullHandler())
    log.propagate = False
    mole.CONFIRM_WINDOW_SEC, mole.CONFIRM_MIN_TRADES, mole.CONFIRM_MIN_DAYVOL_LOTS = 1.0, 1, None
    cases = [("_neg_trade-mole-table_ARTL_2026-10-07_08-48-00.csv", False, "no trade within 1 s -> dropped"),
             ("_pos_trade-mole-table_LABT_2026-10-07_08-02-00.csv", True, "trades follow -> released")]
    for name, expect_fire, why in cases:
        p = os.path.join(OUT_DIR, name)
        if not os.path.exists(p):
            check(f"{name} present", False, "file missing")
            continue
        app, fire = R.replay_file(p, mole, {}, stop_at_trigger=True, log=log)
        check(f"{name[:40]}: {why}", bool(fire) == expect_fire, f"fire={fire and fire['reason']}")
        if fire and expect_fire:
            check("   released reason names the confirmation", "confirmed=" in fire.get("reason", ""),
                  fire.get("reason", ""))
    mole.CONFIRM_WINDOW_SEC = None


def off_case(mole):
    print("switch off")
    app = make_app(mole, CONFIRM_WINDOW_SEC=None)
    check("default constants: CONFIRM_WINDOW_SEC is None", mole.CONFIRM_WINDOW_SEC is None)
    check("no pending state", app._pending_signal is None)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--module", default="trade-mole-2.6.py")
    args = ap.parse_args()
    logging.disable(logging.CRITICAL)
    mole = R.load_mole(args.module)
    saved = {k: getattr(mole, k) for k in ("CONFIRM_WINDOW_SEC", "CONFIRM_MIN_TRADES",
                                           "CONFIRM_MIN_DAYVOL_LOTS", "CONFIRM_RULES")}
    off_case(mole)
    unit_cases(mole)
    tail_cases(mole)
    for k, v in saved.items():
        setattr(mole, k, v)
    print(f"\n{'ALL CHECKS PASSED' if not FAILS else str(len(FAILS)) + ' FAILED: ' + ', '.join(FAILS)}")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
