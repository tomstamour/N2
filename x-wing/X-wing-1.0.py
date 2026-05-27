#!/usr/bin/env python
"""
X-wing-1.0.py - yield-laddered trailing-stop trader for IBKR (ibapi).

One process trades ONE symbol (long side). Run >=10 processes for >=10 symbols.

Design (see plan / x-wing-prompt.txt):
  1. Place a BUY LMT at --Entry-limit-price for floor(--capital / entry) shares.
  2. As it (partially) fills, arm a protective SELL STP LMT for the filled qty.
  3. Every second, sample the streaming L1 BID/ASK midpoint ("current price"),
     compute the position yield vs the (frozen) original average fill price.
  4. Look up --input-limits-table by yield (banded) -> Trigger(%)/Limit(%), then
     auxPrice = mid*(1-Trigger/100), lmtPrice = mid*(1-Limit/100). REPLACE (never
     cancel) the resting protective order with the new aux/lmt and current qty.
  5. Synthetic trigger: when mid <= aux, REPLACE the same order into a marketable
     SELL LMT priced below the bid (bid*(1-exit_cross_percent/100)) to force the
     exit (works in ETH where native stops are unreliable). If it does not fill,
     leave it resting; do not end the script.
  6. When the protective sell fully fills, re-enter with a BUY LMT at the sell
     fill price for the same share count, then re-arm the protective order using
     the SAME original average fill price.

Never uses reqTickByTickData (3-request cap) and never uses reqGlobalCancel
(would nuke other instances' orders on the shared account).

Two ibapi clients per instance:
  - OrderClient  (clientId = --client-id-base)      orders + fills + contract
  - DataClient   (clientId = --client-id-base + 1)   streaming L1 market data
Launch instance N with --client-id-base = 10000 + 2*N (10000, 10002, ...).
"""

import argparse
import csv
import logging
import math
import os
import signal
import sys
import threading
import time
from datetime import datetime, time as dtime
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - fallback if tzdata missing
    ET = None

try:
    import pandas as pd
except ImportError:
    print("Error: pandas not found. Install with: pip install pandas")
    sys.exit(1)

try:
    from ibapi.client import EClient
    from ibapi.wrapper import EWrapper
    from ibapi.contract import Contract
    from ibapi.order import Order
except ImportError:
    print("Error: ibapi module not found. Install with: pip install ibapi")
    sys.exit(1)

logger = logging.getLogger("x-wing")

# ibapi tick types we care about
TICK_BID, TICK_ASK, TICK_LAST, TICK_CLOSE = 1, 2, 4, 9
DELAYED_BID, DELAYED_ASK, DELAYED_LAST, DELAYED_CLOSE = 66, 67, 68, 75
UNSET_DOUBLE = 1.7976931348623157e+308

# error codes that indicate an order modification was rejected (-> cancel+new fallback)
MODIFY_REJECT_CODES = {103, 104, 105, 106, 110, 161, 201, 202, 10147, 10148}

# hard rejections of a new (unfilled) BUY entry/re-entry order -> stop the run
ENTRY_REJECT_CODES = {110, 200, 201, 203}


# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #
def parse_lifetime(s: str) -> int:
    """mm:ss -> total seconds (>0)."""
    try:
        mm, ss = s.split(":")
        total = int(mm) * 60 + int(ss)
        if total <= 0:
            raise ValueError
        return total
    except Exception:
        raise argparse.ArgumentTypeError(
            f"--lifetime must be mm:ss with a positive value, got {s!r}")


def parse_hhmm(s: str) -> dtime:
    """HH:MM (24h, ET) -> datetime.time."""
    try:
        hh, mm = s.split(":")
        return dtime(int(hh), int(mm))
    except Exception:
        raise argparse.ArgumentTypeError(
            f"--session-end must be HH:MM (24h ET), got {s!r}")


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="X-wing-1.0: yield-laddered trailing-stop trader (IBKR/ibapi).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--symbol", required=True, help="Stock ticker, e.g. AAPL")
    p.add_argument("--Entry-limit-price", dest="entry_limit_price", default=None,
                   type=float, help="Fixed buy LMT price for the initial entry. "
                        "Optional fallback; --max-limit-entry-percent-price takes "
                        "precedence and computes the price from the live ask at entry.")
    p.add_argument("--capital", required=True, type=float,
                   help="Dollar budget; shares = floor(capital / entry-limit-price)")
    # --- entry-price computed off the live ask (used at fire/entry time) ---
    p.add_argument("--max-limit-entry-percent-price", dest="max_limit_entry_percent_price",
                   type=float, default=None,
                   help="Buy LMT = ask * (1 + pct/100), priced off the live ask "
                        "(falls back ask->last->close). Takes precedence over "
                        "--Entry-limit-price.")
    p.add_argument("--last-close-price", dest="last_close_price", type=float, default=None,
                   help="Previous session close; with --max-cap-entry-percent forms a "
                        "cap on the entry limit. Both required together.")
    p.add_argument("--max-cap-entry-percent", dest="max_cap_entry_percent", type=float,
                   default=None,
                   help="Entry-limit cap = last-close-price * (1 + pct/100). The computed "
                        "entry limit is lowered to this cap when it exceeds it.")
    # --- prewarm (two-phase startup: connect now, fire/abort on signal) ---
    p.add_argument("--prewarm", action="store_true",
                   help="Two-phase startup: connect, resolve contract, and stream quotes, "
                        "then wait for SIGUSR1 (fire entry) or SIGTERM/SIGINT (abort).")
    p.add_argument("--prewarm-timeout", dest="prewarm_timeout", type=float, default=120.0,
                   help="Seconds to wait in prewarm before auto-aborting (no trade). "
                        "<=0 waits indefinitely.")
    p.add_argument("--account", default=None,
                   help="IBKR account ID whose capital is used for the trade "
                        "(set on every order). Default: the connection's default account.")
    p.add_argument("--input-limits-table", dest="limits_table", required=True,
                   help="TSV with columns: 'Yield (%%)', 'Trigger(%%)', 'Limit(%%)'")
    p.add_argument("--log-dir", dest="log_dir", required=True, help="Log directory")
    p.add_argument("--price-action-table", dest="price_action_table", default=None,
                   help="Output TSV path (file) or directory. Default: --log-dir. "
                        "Auto-name: x-wing-table-SYMBOL-MM-HH-DD-YYYY.tsv")
    p.add_argument("--lifetime", type=parse_lifetime, default=None,
                   help="Run duration mm:ss; on expiry flatten @ bid and exit")
    p.add_argument("--session-end", dest="session_end", type=parse_hhmm, default=None,
                   help="ET session-close HH:MM; flatten @ bid and exit at/after it")
    p.add_argument("--client-id-base", dest="client_id_base", type=int, default=10000,
                   help="Order client uses this id; data client uses +1")
    p.add_argument("--host", default="127.0.0.1", help="TWS/Gateway host")
    p.add_argument("--port", type=int, default=4002,
                   help="IBKR port (4002 paper GW / 4001 live GW / 7497 paper TWS / 7496 live TWS)")
    p.add_argument("--market-data-type", dest="market_data_type", type=int, default=1,
                   choices=[1, 2, 3, 4],
                   help="1=real-time, 2=frozen, 3=delayed, 4=delayed-frozen")
    p.add_argument("--loop-interval", dest="loop_interval", type=float, default=1.0,
                   help="Control-loop / price-sample interval in seconds")
    p.add_argument("--exit-cross-percent", dest="exit_cross_percent", type=float, default=0.5,
                   help="Forced-exit SELL LMT is priced at bid*(1 - pct/100) to cross the "
                        "spread and guarantee a marketable fill (default 0.5)")
    p.add_argument("--loglevel", default="INFO",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p


# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
def setup_logging(log_dir: str, symbol: str, level: str) -> None:
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    log_path = Path(log_dir) / f"x-wing-{symbol}-{datetime.now():%Y-%m-%d}.log"
    logger.setLevel(getattr(logging, level))
    if logger.handlers:
        return
    fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s",
                            datefmt="%H:%M:%S")
    fh = logging.FileHandler(log_path)
    fh.setFormatter(fmt)
    fh.setLevel(logging.DEBUG)
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    ch.setLevel(getattr(logging, level))
    logger.addHandler(fh)
    logger.addHandler(ch)
    logger.info("Log file: %s", log_path.resolve())


# --------------------------------------------------------------------------- #
# Limits table
# --------------------------------------------------------------------------- #
class LimitsTable:
    """Yield -> (Trigger%, Limit%) banded lookup. Ignores any illustrative
    auxPrice($)/lmtPrice($) columns; recomputes from the live midpoint."""

    def __init__(self, path: str):
        df = pd.read_csv(path, sep="\t")
        df.columns = [c.strip() for c in df.columns]
        # tolerate minor header variations
        ycol = self._find(df, ["Yield (%)", "Yield(%)", "Yield"])
        tcol = self._find(df, ["Trigger(%)", "Trigger (%)", "Trigger"])
        lcol = self._find(df, ["Limit(%)", "Limit (%)", "Limit"])
        rows = df[[ycol, tcol, lcol]].dropna().astype(float)
        rows = rows.sort_values(ycol).reset_index(drop=True)
        if rows.empty:
            raise ValueError(f"No usable rows in limits table {path!r}")
        self.yields = rows[ycol].tolist()
        self.triggers = rows[tcol].tolist()
        self.limits = rows[lcol].tolist()
        logger.info("Loaded limits table %s (%d rows, yields %.0f..%.0f%%)",
                    path, len(self.yields), self.yields[0], self.yields[-1])

    @staticmethod
    def _find(df, candidates):
        for c in candidates:
            if c in df.columns:
                return c
        raise ValueError(f"limits table missing a column among {candidates}; "
                         f"found {list(df.columns)}")

    def lookup(self, yield_pct: float):
        """Highest row whose Yield (%) <= yield_pct; floor at the first row."""
        idx = 0
        for i, y in enumerate(self.yields):
            if y <= yield_pct:
                idx = i
            else:
                break
        return self.yields[idx], self.triggers[idx], self.limits[idx]


def round_to_tick(price: float, tick: float) -> float:
    if tick is None or tick <= 0:
        tick = 0.01
    # SEC Rule 612: US NMS stocks priced >= $1.00 must be in $0.01 increments;
    # sub-penny ($0.0001) is allowed only below $1.00. contractDetails.minTick is
    # the finest band, so enforce the penny floor at/above $1.00.
    if price >= 1.0 and tick < 0.01:
        tick = 0.01
    return round(round(price / tick) * tick, 6)


# --------------------------------------------------------------------------- #
# Price-action table writer
# --------------------------------------------------------------------------- #
PA_COLUMNS = [
    "timestamp", "symbol", "phase", "bid", "ask", "mid", "orig_avg_fill",
    "filled_qty", "position", "yield_pct", "row_yield_threshold", "trigger_pct",
    "limit_pct", "aux_price", "lmt_price", "protective_order_id",
    "protective_status", "event",
]


class PriceActionWriter:
    def __init__(self, path_arg, symbol, log_dir):
        if not path_arg:
            path_arg = log_dir
        p = Path(path_arg)
        if p.suffix.lower() == ".tsv":
            self.path = p
        else:  # treat as a directory -> auto-name
            now = datetime.now()
            fname = f"x-wing-table-{symbol}-{now:%m-%H-%d-%Y}.tsv"
            self.path = p / fname
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        new = not self.path.exists()
        self._fh = open(self.path, "a", newline="", encoding="utf-8")
        self._w = csv.DictWriter(self._fh, fieldnames=PA_COLUMNS, delimiter="\t",
                                 extrasaction="ignore")
        if new:
            self._w.writeheader()
            self._fh.flush()
        logger.info("Price-action table: %s", self.path.resolve())

    def write(self, row: dict):
        with self._lock:
            self._w.writerow(row)
            self._fh.flush()

    def close(self):
        try:
            self._fh.close()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# IB clients
# --------------------------------------------------------------------------- #
class OrderClient(EWrapper, EClient):
    """Handles order placement/modification, fills, and contract resolution."""

    def __init__(self, controller):
        EClient.__init__(self, self)
        self.ctl = controller
        self.connected_evt = threading.Event()
        self.contract_evt = threading.Event()

    # ---- connection / ids ----
    def nextValidId(self, orderId: int):
        self.ctl.set_next_order_id(orderId)
        if not self.connected_evt.is_set():
            logger.info("OrderClient connected; nextValidId=%d", orderId)
            self.connected_evt.set()

    def contractDetails(self, reqId: int, contractDetails):
        self.ctl.set_contract_details(contractDetails)
        self.contract_evt.set()

    def contractDetailsEnd(self, reqId: int):
        self.contract_evt.set()

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        info = {2104, 2106, 2107, 2108, 2109, 2119, 2158, 2100, 2150, 399}
        msg = f"IB msg reqId={reqId} code={errorCode}: {errorString}"
        if errorCode in info:
            logger.info(msg)
        else:
            logger.warning(msg)
        self.ctl.on_order_error(reqId, errorCode, errorString)

    # ---- order callbacks ----
    def orderStatus(self, orderId, status, filled, remaining, avgFillPrice,
                    permId, parentId, lastFillPrice, clientId, whyHeld,
                    mktCapPrice=0.0):
        self.ctl.on_order_status(orderId, status, float(filled),
                                 float(avgFillPrice), float(lastFillPrice))

    def openOrder(self, orderId, contract, order, orderState):
        logger.debug("openOrder %d %s %s qty=%s status=%s", orderId, order.action,
                     order.orderType, order.totalQuantity, orderState.status)

    def execDetails(self, reqId, contract, execution):
        logger.info("exec %s %s %s @ %.4f (orderId=%d)", execution.side,
                    execution.shares, contract.symbol, execution.price,
                    execution.orderId)


class DataClient(EWrapper, EClient):
    """Streaming L1 market data; maintains latest bid/ask/last/close."""

    def __init__(self, controller):
        EClient.__init__(self, self)
        self.ctl = controller
        self.connected_evt = threading.Event()

    def nextValidId(self, orderId: int):
        if not self.connected_evt.is_set():
            logger.info("DataClient connected; nextValidId=%d", orderId)
            self.connected_evt.set()

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        info = {2104, 2106, 2107, 2108, 2109, 2119, 2158, 2100, 2150, 10167, 10197}
        msg = f"IB(data) reqId={reqId} code={errorCode}: {errorString}"
        if errorCode in info:
            logger.info(msg)
        else:
            logger.warning(msg)

    def tickPrice(self, reqId, tickType, price, attrib):
        if price is None or price <= 0 or price == UNSET_DOUBLE:
            return
        if tickType in (TICK_BID, DELAYED_BID):
            self.ctl.update_quote(bid=price)
        elif tickType in (TICK_ASK, DELAYED_ASK):
            self.ctl.update_quote(ask=price)
        elif tickType in (TICK_LAST, DELAYED_LAST):
            self.ctl.update_quote(last=price)
        elif tickType in (TICK_CLOSE, DELAYED_CLOSE):
            self.ctl.update_quote(close=price)


# --------------------------------------------------------------------------- #
# Controller / state machine
# --------------------------------------------------------------------------- #
PHASE_INIT = "INIT"
PHASE_PREWARM = "PREWARM"
PHASE_ENTRY_PENDING = "ENTRY_PENDING"
PHASE_ENTRY_PARTIAL = "ENTRY_PARTIAL"
PHASE_LONG_MONITOR = "LONG_MONITOR"
PHASE_EXITING = "EXITING"
PHASE_RE_ENTRY_PENDING = "RE_ENTRY_PENDING"
PHASE_FLATTEN = "FLATTEN"
PHASE_SHUTDOWN = "SHUTDOWN"


class XWingController:
    def __init__(self, args, table: LimitsTable, pa_writer: PriceActionWriter):
        self.args = args
        self.symbol = args.symbol.upper()
        self.table = table
        self.pa = pa_writer

        self.lock = threading.RLock()
        self.wake = threading.Event()      # interruptible sleep for the main loop
        self.stop_evt = threading.Event()  # set on signal / lifetime / session end
        self.fire_evt = threading.Event()  # set on SIGUSR1 (or abort) to leave prewarm
        self.fire_requested = False        # True only when SIGUSR1 asked to fire

        self.order_client = None
        self.data_client = None

        # contract
        self.contract = None
        self.min_tick = 0.01

        # quotes
        self.bid = self.ask = self.last = self.close = None

        # order-id allocation (order client)
        self.next_order_id = None

        # order bookkeeping: orderId -> dict
        self.orders = {}
        self.entry_shares = 0
        self.active_buy_id = None
        self.protective_id = None
        self.original_avg_fill = None   # frozen after first full entry fill
        self.running_entry_avg = None
        self.position = 0.0
        self.phase = PHASE_INIT

        # protective order's last sent state (to skip redundant replaces)
        self._prot_sent = None  # (otype, aux, lmt, qty)
        self.exiting = False    # protective order has been converted to exit LMT
        self.terminating = False

        # ratchet state (stop holds when price falls, only moves UP) - per cycle
        self.high_water_mid = None   # peak mid since the current position opened
        self._aux_floor = None       # highest aux ever computed this cycle
        self._lmt_floor = None       # highest lmt ever computed this cycle

        # re-entry signalling
        self.pending_reentry = False
        self.reentry_price = None
        self.reentry_shares = 0

    # ---------------------- connection helpers ----------------------
    def set_next_order_id(self, oid):
        with self.lock:
            if self.next_order_id is None or oid > self.next_order_id:
                self.next_order_id = oid

    def _alloc_id(self):
        with self.lock:
            oid = self.next_order_id
            self.next_order_id += 1
            return oid

    def set_contract_details(self, cd):
        with self.lock:
            self.contract = cd.contract
            try:
                if cd.minTick and cd.minTick > 0:
                    self.min_tick = cd.minTick
            except Exception:
                pass
            logger.info("Contract resolved: %s conId=%s primaryExch=%s minTick=%s",
                        self.contract.symbol, self.contract.conId,
                        self.contract.primaryExchange, self.min_tick)

    # ---------------------- market data ----------------------
    def update_quote(self, bid=None, ask=None, last=None, close=None):
        with self.lock:
            if bid is not None:
                self.bid = bid
            if ask is not None:
                self.ask = ask
            if last is not None:
                self.last = last
            if close is not None:
                self.close = close

    def current_mid(self):
        """BID/ASK midpoint; fall back to last, then close."""
        with self.lock:
            if self.bid and self.ask and self.bid > 0 and self.ask > 0:
                return (self.bid + self.ask) / 2.0
            if self.last and self.last > 0:
                return self.last
            if self.close and self.close > 0:
                return self.close
            return None

    def _exit_cross_price(self):
        """Marketable SELL price: cross down through the bid so the limit fills.
        bid*(1 - exit_cross_percent/100); fall back bid -> mid -> last -> close."""
        with self.lock:
            buf = max(0.0, getattr(self.args, "exit_cross_percent", 0.5)) / 100.0
            if self.bid and self.bid > 0:
                return self.bid * (1 - buf)
            return self.current_mid()  # current_mid already does mid->last->close

    # ---------------------- order construction ----------------------
    def _base_order(self, action, qty, otype):
        o = Order()
        o.action = action
        o.orderType = otype
        o.totalQuantity = int(qty)
        o.tif = "GTC"
        o.outsideRth = True
        o.transmit = True
        o.eTradeOnly = False     # default True in ibapi 9.81 -> would reject
        o.firmQuoteOnly = False
        if self.args.account:
            o.account = self.args.account
        return o

    def limit_order(self, action, qty, lmt):
        o = self._base_order(action, qty, "LMT")
        o.lmtPrice = round_to_tick(lmt, self.min_tick)
        return o

    def stp_lmt_order(self, action, qty, aux, lmt):
        o = self._base_order(action, qty, "STP LMT")
        o.auxPrice = round_to_tick(aux, self.min_tick)
        o.lmtPrice = round_to_tick(lmt, self.min_tick)
        return o

    # ---------------------- placing ----------------------
    def compute_entry_limit_price(self):
        """Determine the BUY LMT price for the initial entry, evaluated now.

        If --max-limit-entry-percent-price is set, price off the live ask
        (falling back ask->last->close): ref * (1 + pct/100), then lower to the
        cap = last_close_price * (1 + max_cap_entry_percent/100) when both cap
        args are given. Otherwise use the fixed --Entry-limit-price. Returns
        None when no price can be determined."""
        with self.lock:
            a = self.args
            if a.max_limit_entry_percent_price is not None:
                ref = src = None
                if self.ask and self.ask > 0:
                    ref, src = self.ask, "ask"
                elif self.last and self.last > 0:
                    ref, src = self.last, "last"
                elif self.close and self.close > 0:
                    ref, src = self.close, "close"
                if ref is None:
                    if a.entry_limit_price is not None:
                        logger.warning("No ask/last/close at entry; using fixed "
                                       "--Entry-limit-price %.4f", a.entry_limit_price)
                        return round_to_tick(a.entry_limit_price, self.min_tick)
                    logger.error("No ask/last/close and no --Entry-limit-price; "
                                 "cannot price entry")
                    return None
                price = ref * (1 + a.max_limit_entry_percent_price / 100.0)
                if a.last_close_price is not None and a.max_cap_entry_percent is not None:
                    cap = a.last_close_price * (1 + a.max_cap_entry_percent / 100.0)
                    if price > cap:
                        logger.info("Entry %.4f capped to %.4f (last_close %.4f + %.1f%%)",
                                    price, cap, a.last_close_price, a.max_cap_entry_percent)
                        price = cap
                price = round_to_tick(price, self.min_tick)
                logger.info("Entry price: %s=%.4f * (1+%.2f%%) -> %.4f", src, ref,
                            a.max_limit_entry_percent_price, price)
                return price
            if a.entry_limit_price is not None:
                return round_to_tick(a.entry_limit_price, self.min_tick)
            return None

    def place_entry(self):
        with self.lock:
            price = self.compute_entry_limit_price()
            if price is None or price <= 0:
                logger.error("Cannot determine entry limit price; aborting entry")
                self.stop_evt.set()
                return
            shares = int(math.floor(self.args.capital / price))
            if shares < 1:
                logger.error("capital %.2f / entry %.4f < 1 share; nothing to do",
                             self.args.capital, price)
                self.stop_evt.set()
                return
            self.entry_shares = shares
            oid = self._alloc_id()
            self.active_buy_id = oid
            self.orders[oid] = dict(action="BUY", otype="LMT", status="Submitted",
                                    filled=0.0, avg=0.0, role="entry")
            order = self.limit_order("BUY", shares, price)
            logger.info("ENTRY: BUY LMT %d %s @ %.4f (orderId=%d)", shares,
                        self.symbol, order.lmtPrice, oid)
            self.order_client.placeOrder(oid, self.contract, order)
            self.phase = PHASE_ENTRY_PENDING

    def place_reentry(self, price, shares):
        with self.lock:
            oid = self._alloc_id()
            self.active_buy_id = oid
            self.orders[oid] = dict(action="BUY", otype="LMT", status="Submitted",
                                    filled=0.0, avg=0.0, role="reentry")
            order = self.limit_order("BUY", shares, price)
            logger.info("RE-ENTRY: BUY LMT %d %s @ %.4f (orderId=%d)", shares,
                        self.symbol, order.lmtPrice, oid)
            self.order_client.placeOrder(oid, self.contract, order)
            self.phase = PHASE_RE_ENTRY_PENDING

    def compute_protective_levels(self, mid):
        """Single source of truth for the protective STP LMT levels.

        Tracks a high-water mid and RATCHETS the result: aux/lmt only ever move
        UP and HOLD when price falls, so the resting STP LMT behaves like a true
        native stop-limit (it actually triggers when price drops to aux) instead
        of floating down with the price. Returns (row_yield, trig%, lim%, aux, lmt)."""
        with self.lock:
            ref = self.original_avg_fill or self.running_entry_avg or mid
            self.high_water_mid = max(self.high_water_mid or mid, mid)
            hw = self.high_water_mid
            ypct = (hw - ref) / ref * 100.0 if ref else 0.0
            ythr, trig, lim = self.table.lookup(ypct)
            aux = round_to_tick(hw * (1 - trig / 100.0), self.min_tick)
            lmt = round_to_tick(hw * (1 - lim / 100.0), self.min_tick)
            # ratchet: the stop only moves UP; it holds when price falls
            if self._aux_floor is not None:
                aux = max(aux, self._aux_floor)
            if self._lmt_floor is not None:
                lmt = max(lmt, self._lmt_floor)
            lmt = min(lmt, aux)          # keep limit at/below trigger (boundary safety)
            self._aux_floor, self._lmt_floor = aux, lmt
            return ythr, trig, lim, aux, lmt

    def arm_or_replace_protective(self, mid, *, aux=None, lmt=None,
                                  force_exit=False, exit_lmt=None, reason=""):
        """Place/replace the single resting protective SELL order (same orderId
        => modify, never cancel). When force_exit, convert it to a SELL LMT to
        force the exit: at the held table limit (exit_lmt, a synthetic stop
        trigger) or, when none is given, the marketable bid-cross price used to
        flatten (see _exit_cross_price)."""
        with self.lock:
            qty = int(round(self.position))
            if qty <= 0:
                return
            if force_exit:
                px = exit_lmt if exit_lmt is not None else (self._exit_cross_price() or mid)
                otype, aux, lmt = "LMT", None, round_to_tick(px, self.min_tick)
            else:
                if aux is None or lmt is None:
                    _, _, _, aux, lmt = self.compute_protective_levels(mid)
                otype = "STP LMT"

            sent = (otype, aux, lmt, qty)
            if sent == self._prot_sent and not force_exit:
                return  # nothing changed; skip redundant replace

            if self.protective_id is None:
                self.protective_id = self._alloc_id()
            oid = self.protective_id

            if otype == "LMT":
                order = self.limit_order("SELL", qty, lmt)
            else:
                order = self.stp_lmt_order("SELL", qty, aux, lmt)

            self.orders.setdefault(oid, dict(action="SELL", filled=0.0, avg=0.0,
                                             role="protective"))
            self.orders[oid].update(otype=otype, status="WorkingMod")
            self._prot_sent = sent
            self.order_client.placeOrder(oid, self.contract, order)
            verb = "EXIT->LMT@bid" if force_exit else "replace"
            logger.info("STOP %s id=%d SELL %s qty=%d aux=%s lmt=%s %s", verb, oid,
                        otype, qty, aux, lmt, f"({reason})" if reason else "")

    # ---------------------- callbacks ----------------------
    def on_order_status(self, orderId, status, filled, avg, last_fill):
        with self.lock:
            o = self.orders.get(orderId)
            if o is None:
                return
            prev_filled = o.get("filled", 0.0)
            o["status"] = status
            o["filled"] = filled
            o["avg"] = avg
            newly = filled - prev_filled

            if o["action"] == "BUY":
                if newly > 0:
                    self.position += newly
                    self.running_entry_avg = avg or self.running_entry_avg
                if orderId == self.active_buy_id and o.get("role") == "entry":
                    if status == "Filled" and self.original_avg_fill is None:
                        self.original_avg_fill = avg
                        logger.info("Entry FILLED: %d sh, original avg fill %.4f",
                                    int(filled), avg)
                if newly > 0:
                    self.exiting = False
                    self.wake.set()  # arm/replace protective ASAP
                logger.info("BUY status id=%d %s filled=%.0f avg=%.4f", orderId,
                            status, filled, avg)

            elif o["action"] == "SELL":
                if newly > 0:
                    self.position -= newly
                    logger.info("STOP fill id=%d %s sold=%.0f @ %.4f pos=%.0f",
                                orderId, status, newly, last_fill or avg,
                                self.position)
                if status == "Filled" and orderId == self.protective_id:
                    # protective fully closed -> re-entry (unless terminating)
                    self.reentry_price = avg or last_fill
                    self.reentry_shares = int(round(filled))
                    self._prot_sent = None
                    self.protective_id = None
                    self.exiting = False
                    # fresh ratchet for the next position cycle
                    self.high_water_mid = None
                    self._aux_floor = None
                    self._lmt_floor = None
                    if not self.terminating:
                        self.pending_reentry = True
                        logger.info("Protective filled; queue RE-ENTRY %d @ %.4f",
                                    self.reentry_shares, self.reentry_price)
                    self.wake.set()

    def on_order_error(self, reqId, code, msg):
        with self.lock:
            rec = self.orders.get(reqId)
            if (rec and rec.get("role") in ("entry", "reentry")
                    and rec.get("filled", 0.0) <= 0
                    and code in ENTRY_REJECT_CODES):
                logger.error("Entry/re-entry order %d rejected (code %d): %s; aborting run",
                             reqId, code, msg)
                rec["status"] = "Rejected"
                self.stop_evt.set()
                self.wake.set()  # interrupt the 1 Hz control-loop sleep so it exits promptly
                return
            if reqId == self.protective_id and self.exiting and code in MODIFY_REJECT_CODES:
                # documented fallback: type-change modify rejected -> cancel+new
                logger.warning("Protective modify rejected (code %d); cancel+new exit",
                               code)
                try:
                    self.order_client.cancelOrder(self.protective_id)
                except Exception:
                    pass
                # reuse the held stop-limit price so the fallback matches the
                # triggered exit; fall back to the marketable bid-cross if unset
                px = self._lmt_floor if self._lmt_floor is not None else self._exit_cross_price()
                qty = int(round(self.position))
                if qty > 0 and px:
                    oid = self._alloc_id()
                    self.protective_id = oid
                    self.orders[oid] = dict(action="SELL", otype="LMT",
                                            status="Submitted", filled=0.0, avg=0.0,
                                            role="protective")
                    self.order_client.placeOrder(oid, self.contract,
                                                 self.limit_order("SELL", qty, px))
                    self._prot_sent = ("LMT", None, round_to_tick(px, self.min_tick), qty)

    # ---------------------- session helpers ----------------------
    def now_et(self):
        return datetime.now(ET) if ET else datetime.now()

    def is_rth(self):
        n = self.now_et()
        if n.weekday() >= 5:
            return False
        return dtime(9, 30) <= n.time() < dtime(16, 0)

    def session_label(self):
        return "RTH" if self.is_rth() else "ETH"

    def session_end_reached(self):
        if self.args.session_end is None:
            return False
        return self.now_et().time() >= self.args.session_end


# --------------------------------------------------------------------------- #
# Main control loop
# --------------------------------------------------------------------------- #
def control_loop(ctl: XWingController, start_ts: float):
    a = ctl.args
    while not ctl.stop_evt.is_set():
        ctl.wake.clear()
        mid = ctl.current_mid()

        with ctl.lock:
            bid, ask = ctl.bid, ctl.ask
            pos = int(round(ctl.position))
            ref = ctl.original_avg_fill or ctl.running_entry_avg
            ypct = ((mid - ref) / ref * 100.0) if (mid and ref) else None
            event = ""

            # re-entry
            if ctl.pending_reentry and not ctl.terminating:
                ctl.pending_reentry = False
                ctl.place_reentry(ctl.reentry_price, ctl.reentry_shares)
                event = "reentry_placed"

            # protective management while long
            row_thr = trig = lim = aux = lmt = None
            if pos > 0 and mid is not None:
                if ctl.exiting:
                    # leave the resting exit LMT untouched (spec step 5); show held levels
                    row_thr, trig, lim = ctl.table.lookup(ypct if ypct is not None else 0.0)
                    aux, lmt = ctl._aux_floor, ctl._lmt_floor
                else:
                    row_thr, trig, lim, aux, lmt = ctl.compute_protective_levels(mid)
                    if mid <= aux:
                        # synthetic stop trigger: place the SELL limit at the held
                        # table limit (true stop-limit; rests if it gaps below - step 5)
                        ctl.arm_or_replace_protective(mid, force_exit=True, exit_lmt=lmt,
                                                      reason="synthetic-trigger")
                        ctl.exiting = True
                        ctl.phase = PHASE_EXITING
                        event = "stop_triggered"
                    else:
                        ctl.arm_or_replace_protective(mid, aux=aux, lmt=lmt, reason="trail")
                        ctl.phase = (PHASE_LONG_MONITOR if ctl.original_avg_fill
                                     else PHASE_ENTRY_PARTIAL)

            prot = ctl.orders.get(ctl.protective_id, {})
            row = dict(
                timestamp=datetime.now().isoformat(timespec="seconds"),
                symbol=ctl.symbol, phase=ctl.phase,
                bid=_f(bid), ask=_f(ask), mid=_f(mid),
                orig_avg_fill=_f(ctl.original_avg_fill), filled_qty=pos,
                position=pos, yield_pct=_f(ypct, 2),
                row_yield_threshold=_f(row_thr, 1), trigger_pct=_f(trig, 2),
                limit_pct=_f(lim, 2), aux_price=_f(aux), lmt_price=_f(lmt),
                protective_order_id=ctl.protective_id,
                protective_status=prot.get("status", ""), event=event,
            )

        ctl.pa.write(row)
        if mid is not None:
            logger.info("%s %s | mid=%s yld=%s | %s | STOP aux=%s lmt=%s | pos=%d @avg%s",
                        ctl.session_label(), ctl.symbol, _f(mid),
                        (f"{ypct:+.2f}%" if ypct is not None else "n/a"),
                        (f"row({row_thr:.0f}%) {trig}/{lim}" if row_thr is not None else "no-pos"),
                        _f(aux), _f(lmt), pos, _f(ctl.original_avg_fill))
        else:
            logger.warning("%s %s | NO market data yet", ctl.session_label(), ctl.symbol)

        # termination conditions
        if a.lifetime is not None and (time.time() - start_ts) >= a.lifetime:
            logger.info("Lifetime %ds reached -> shutdown", a.lifetime)
            ctl.stop_evt.set()
            break
        if ctl.session_end_reached():
            logger.info("Session-end %s ET reached -> shutdown", a.session_end)
            ctl.stop_evt.set()
            break

        ctl.wake.wait(timeout=a.loop_interval)


def _f(v, nd=4):
    if v is None:
        return ""
    try:
        return f"{float(v):.{nd}f}"
    except Exception:
        return str(v)


# --------------------------------------------------------------------------- #
# Shutdown
# --------------------------------------------------------------------------- #
def shutdown(ctl: XWingController):
    with ctl.lock:
        ctl.terminating = True
        ctl.phase = PHASE_FLATTEN
        pos = int(round(ctl.position))
        mid = ctl.current_mid()
    if pos > 0 and mid is not None:
        logger.info("FLATTEN: closing %d sh with marketable SELL LMT @ bid", pos)
        ctl.arm_or_replace_protective(mid, force_exit=True, reason="flatten")
        # best-effort wait for the close to fill
        deadline = time.time() + 10
        while time.time() < deadline:
            with ctl.lock:
                if int(round(ctl.position)) <= 0:
                    break
            time.sleep(0.25)
        with ctl.lock:
            left = int(round(ctl.position))
        if left > 0:
            logger.warning("FLATTEN incomplete: %d sh still open; SELL LMT left "
                           "resting at the broker", left)

    with ctl.lock:
        ctl.phase = PHASE_SHUTDOWN
    # NOTE: deliberately NO reqGlobalCancel (would hit other instances).
    for client, name in ((ctl.data_client, "data"), (ctl.order_client, "order")):
        try:
            if client and client.isConnected():
                if name == "data" and ctl.contract is not None:
                    try:
                        client.cancelMktData(1)
                    except Exception:
                        pass
                client.disconnect()
                logger.info("%s client disconnected", name)
        except Exception as e:
            logger.error("Error disconnecting %s client: %s", name, e)
    ctl.pa.close()


# --------------------------------------------------------------------------- #
# Wiring
# --------------------------------------------------------------------------- #
def make_contract(symbol):
    c = Contract()
    c.symbol = symbol
    c.secType = "STK"
    c.exchange = "SMART"
    c.currency = "USD"
    return c


def connect_client(client, host, port, client_id, ready_evt, label, timeout=15):
    logger.info("Connecting %s client clientId=%d to %s:%d", label, client_id, host, port)
    client.connect(host, port, client_id)
    t = threading.Thread(target=client.run, daemon=True, name=f"{label}-run")
    t.start()
    if not ready_evt.wait(timeout=timeout):
        raise RuntimeError(f"{label} client did not connect within {timeout}s")
    return t


def main():
    args = build_arg_parser().parse_args()
    symbol = args.symbol.upper()
    setup_logging(args.log_dir, symbol, args.loglevel)

    if args.port in (4001, 7496):
        logger.warning("PORT %d looks like a LIVE trading port. Real money at risk.",
                       args.port)
    if args.account:
        logger.info("Orders will be placed in account %s", args.account)

    # entry-pricing validation: need at least one way to price the entry
    if args.max_limit_entry_percent_price is None and args.entry_limit_price is None:
        logger.error("No entry price source: pass --max-limit-entry-percent-price "
                     "(computed off the ask) or --Entry-limit-price (fixed).")
        sys.exit(2)
    # the cap requires BOTH last-close-price and max-cap-entry-percent
    if (args.last_close_price is None) != (args.max_cap_entry_percent is None):
        logger.error("--last-close-price and --max-cap-entry-percent must be given "
                     "together to enable the entry-limit cap.")
        sys.exit(2)

    table = LimitsTable(args.limits_table)
    pa = PriceActionWriter(args.price_action_table, symbol, args.log_dir)
    ctl = XWingController(args, table, pa)

    ctl.order_client = OrderClient(ctl)
    ctl.data_client = DataClient(ctl)

    # signal handling -> graceful shutdown (also = prewarm "abort")
    def _sig(signum, frame):
        logger.info("Signal %s received -> shutdown", signum)
        ctl.stop_evt.set()
        ctl.wake.set()
        ctl.fire_evt.set()  # unblock a prewarm wait so it sees stop_evt and aborts
    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    # prewarm "fire" trigger: SIGUSR1 places the entry. Register BEFORE connecting
    # so an early signal isn't lost (default action for SIGUSR1 would kill us).
    if args.prewarm and hasattr(signal, "SIGUSR1"):
        def _fire(signum, frame):
            logger.info("SIGUSR1 received -> FIRE entry")
            ctl.fire_requested = True
            ctl.fire_evt.set()
        signal.signal(signal.SIGUSR1, _fire)

    try:
        connect_client(ctl.order_client, args.host, args.port,
                       args.client_id_base, ctl.order_client.connected_evt, "order")

        # resolve contract (+ minTick) on the order client
        ctl.order_client.contract_evt.clear()
        ctl.order_client.reqContractDetails(1, make_contract(symbol))
        if not ctl.order_client.contract_evt.wait(timeout=15) or ctl.contract is None:
            raise RuntimeError(f"Could not resolve contract for {symbol}")

        connect_client(ctl.data_client, args.host, args.port,
                       args.client_id_base + 1, ctl.data_client.connected_evt, "data")

        ctl.data_client.reqMarketDataType(args.market_data_type)
        ctl.data_client.reqMktData(1, ctl.contract, "", False, False, [])

        # wait for a usable price, with a delayed-data fallback
        logger.info("Waiting for first market-data tick...")
        if not _wait_for_mid(ctl, 10):
            if args.market_data_type == 1:
                logger.warning("No real-time data; retrying with delayed (type 3)")
                ctl.data_client.reqMarketDataType(3)
                _wait_for_mid(ctl, 10)
        if ctl.current_mid() is None:
            logger.warning("Proceeding without a confirmed quote; entry uses limit only")

        proceed = True
        if args.prewarm:
            ctl.phase = PHASE_PREWARM
            t = args.prewarm_timeout
            logger.info("PREWARM ready for %s; waiting for fire (SIGUSR1) or abort "
                        "(SIGTERM)%s", symbol,
                        f"; auto-abort in {t:.0f}s" if (t and t > 0) else "")
            deadline = (time.time() + t) if (t and t > 0) else None
            while not ctl.fire_requested and not ctl.stop_evt.is_set():
                if deadline and time.time() >= deadline:
                    break
                ctl.fire_evt.wait(timeout=0.2)
            if ctl.stop_evt.is_set():
                logger.info("Abort received during prewarm; disconnecting without trading")
                proceed = False
            elif not ctl.fire_requested:
                logger.warning("PREWARM timeout (%.0fs) with no fire/abort; auto-aborting", t)
                proceed = False
            else:
                logger.info("FIRE: placing entry for %s", symbol)

        if proceed:
            ctl.place_entry()
            control_loop(ctl, start_ts=time.time())
    except Exception as e:
        logger.exception("Fatal error: %s", e)
    finally:
        shutdown(ctl)
        # join run threads briefly
        time.sleep(0.5)
    logger.info("X-wing exited.")


def _wait_for_mid(ctl, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if ctl.current_mid() is not None:
            return True
        time.sleep(0.2)
    return False


if __name__ == "__main__":
    main()
