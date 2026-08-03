#!/usr/bin/env python3
"""
clerk-1.2.py — orchestrator-facing client pool that runs trade-mole + T47 +
x-wing TRIOS in-process on ONE shared ibapi connection each.

Why this exists
---------------
clerk-1.1 ran a DUO per trigger: one trade-mole surge -> one x-wing run ->
session over. 1.2 adds T47 (T47-airspeeder-2.0.py), a re-entry controller
that, after the x-wing exit, keeps watching the bid and grants ONE re-entry
(t47.XWING_REENTRY_NUMBER) with a FRESH x-wing when the bid dips at least
EP2_MIN_DIP_PCT below the exit price and then comes back up to it
(thresholds/lifetime at the top of the T47 script). clerk-1.1.py stays on
disk unchanged as the live fallback.

    orchestrator ──TCP/JSON──► clerk
        {"ticker","lastDailyClose","itiBaseline","tradeSizeBaseline"}
    clerk picks a FREE warm client, then per client (a "trio"):
        SharedIBClient (one socket, one run() thread)
          ├─ tick callbacks ─► trade-mole (record + detect surge)
          │                 ├► x-wing     (CURRENT run: quotes / entry ref)
          │                 └► T47        (bid watch: dip-then-recross re-entry)
          └─ order callbacks ► x-wing (current run) + T47 echo (EP/SP capture)
        trade-mole surge ──► T47.launch_run1 ──► x-wing.on_buy_signal(...)
        x-wing run done ──► T47.on_run_done: open the WATCH (one fresh x-wing
            when the bid returns to SP1) or session.finish("t47:...")

`session.xwing` always points at the CURRENT run's controller; T47 swaps it
per relaunch, which atomically re-points the order/tick routing below. The
client is held for the WHOLE trio (watch included) and only on finish() is it
RESET (cancelMktData + clear state) and returned to the pool WITHOUT dropping
the socket.

Startup
-------
    python3 clerk-1.2.py --client-qty 10 --port 4001 --listen-port 8765

Trigger (from the orchestrator, one JSON object per line)::

    echo '{"ticker":"JWEL","lastDailyClose":2.35,"itiBaseline":44444.0,"tradeSizeBaseline":150.0}' \
        | nc 127.0.0.1 8765
"""

import argparse
import importlib.util
import json
import logging
import os
import signal
import socket
import sys
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, time as dtime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

try:
    from ibapi.client import EClient
    from ibapi.wrapper import EWrapper
except ImportError:
    print("Error: ibapi module not found. Install with: pip install ibapi")
    sys.exit(1)

logger = logging.getLogger("clerk")

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
HERE = Path(__file__).resolve().parent
SCRIPTS_DIR = HERE.parent

# Centralized per-user config (scripts/config/n2_config_file.txt): IBKR Gateway
# host/port and the clerk's own listen host/port are sourced from here as the
# argparse DEFAULTS below (explicit CLI flags still override).
_cfg_spec = importlib.util.spec_from_file_location(
    "n2_config", SCRIPTS_DIR / "config" / "n2_config.py")
n2_config = importlib.util.module_from_spec(_cfg_spec)
_cfg_spec.loader.exec_module(n2_config)
_CFG = n2_config.load_config()

CLIENT_ID_START = 1000          # pool uses 1000 .. 1000 + qty - 1
CLIENT_ID_MAX = 1029            # inclusive ceiling (30 clients)
MKT_REQ_ID = 99                 # per-client market-data reqId (one duo at a time)
CD_REQ_ID = 98                  # per-client contractDetails reqId

# clerk's own daily log (one file per day, rolled at midnight). Anchored to the
# script dir (like CLERK_STATE_DIR below) so it lands beside the clerk regardless
# of the launch CWD.
CLERK_LOG_DIR = str(HERE / "clerk_logs")

# durable queue of deferred (closed-market) duos waiting for the next market
# open. Persisted so a clerk crash/restart while duos are deferred does not lose
# them: they are replayed and re-armed on startup. See PendingStore / replay_pending.
CLERK_STATE_DIR = "clerk_state"
PENDING_DUOS_FILE = "pending_duos.json"

# reconnection watchdog (re-warms pool clients after the daily Gateway restart)
WATCHDOG_INTERVAL_S       = 30   # poll period for the reconnection watchdog
RECONNECT_CONNECT_TIMEOUT = 10   # per-attempt wait for connected_evt
RECONNECT_BACKOFF_S       = 10   # sleep between reconnect attempts
MAX_RECONNECT_ATTEMPTS    = 18   # ~3 min of attempts per watchdog tick

# trade-mole driven-mode artifacts (anchored to the script dir, CWD-independent)
MOLE_OUTPUT_DIR = str(HERE / "mole-outputs")
MOLE_LOG_DIR = str(HERE / "mole-logs")
MOLE_LIFETIME_STR = "02:30"     # built-in watch window (mm:ss) when no
                                # trigger-conditions file/DEFAULT applies
# per-ET-window trigger conditions (mole lifeTime + finbert_gate), loaded ONCE
# at startup. Missing/malformed file -> built-ins (MOLE_LIFETIME_STR, gate on).
TRIGGER_CONDITIONS_FILE = str(HERE / "clerk-trigger-conditions.txt")
DEFAULT_ITI_BASELINE = 44444.00  # used when the orch-trigger has no ITI baseline
DEFAULT_TRADE_SIZE_BASELINE = 44444.00  # used when the trigger has no trade-size baseline
                                        # (the 44444 sentinel disables the M9 columns)
SENTIMENT_TIMEOUT_S  = 2.0       # seconds to wait for Sentiment gate after mole fires


# --------------------------------------------------------------------------- #
# Dynamic import of the hyphen/dot-named sibling modules
# --------------------------------------------------------------------------- #
def _load_module(mod_name: str, filename: str):
    path = HERE / filename
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {filename} from {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


xwing = _load_module("xwing2", "x-wing-2.1.py")
trademole = _load_module("trademole2", "trade-mole-2.2.py")
t47mod = _load_module("t47_airspeeder", "T47-airspeeder-2.0.py")


# --------------------------------------------------------------------------- #
# Per-ET-window trigger conditions (clerk-trigger-conditions.txt)
#
# File format (loaded ONCE at startup; '#' comments allowed; times ET HH:MM;
# window end is EXCLUSIVE — 06:59 means DEFAULT resumes at exactly 06:59:00):
#
#     DEFAULT              lifeTime=02:30  finbert_gate=on
#     WINDOW 04:00-06:59   lifeTime=00:10  finbert_gate=off
#
# lifeTime     mole watch window (mm:ss), resolved when collection starts.
# finbert_gate on|off — whether the Sentiment/FinBERT gate must confirm before
#              x-wing places the order, resolved at the surge instant.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Window:
    start: dtime                    # inclusive
    end: dtime                      # EXCLUSIVE
    lifetime_s: "int | None"        # None = inherit from DEFAULT
    finbert_gate: "bool | None"     # None = inherit from DEFAULT
    label: str                      # "HH:MM-HH:MM" for logs


@dataclass(frozen=True)
class Resolved:
    lifetime_s: int
    finbert_gate: bool
    window: str                     # matched window label, or "DEFAULT"


@dataclass(frozen=True)
class TriggerConditions:
    default_lifetime_s: int
    default_finbert_gate: bool
    windows: tuple = ()
    source: str = "builtin"

    @classmethod
    def builtin(cls):
        return cls(trademole.parse_lifetime(MOLE_LIFETIME_STR), True)

    def at(self, dt_et: datetime) -> Resolved:
        """Resolve the regime in force at the given ET instant."""
        t = dt_et.time()
        for w in self.windows:
            if w.start <= t < w.end:
                life = w.lifetime_s if w.lifetime_s is not None \
                    else self.default_lifetime_s
                gate = w.finbert_gate if w.finbert_gate is not None \
                    else self.default_finbert_gate
                return Resolved(life, gate, w.label)
        return Resolved(self.default_lifetime_s, self.default_finbert_gate,
                        "DEFAULT")


def _parse_condition_keys(tokens, lineno):
    """Parse 'lifeTime=mm:ss' / 'finbert_gate=on|off' tokens; returns
    (lifetime_s_or_None, gate_or_None). Raises ValueError on anything else."""
    life, gate = None, None
    for tok in tokens:
        key, sep, val = tok.partition("=")
        if not sep:
            raise ValueError(f"line {lineno}: expected key=value, got {tok!r}")
        if key == "lifeTime":
            if life is not None:
                raise ValueError(f"line {lineno}: duplicate lifeTime")
            life = trademole.parse_lifetime(val)    # mm:ss -> seconds
        elif key == "finbert_gate":
            if gate is not None:
                raise ValueError(f"line {lineno}: duplicate finbert_gate")
            v = val.lower()
            if v not in ("on", "off"):
                raise ValueError(
                    f"line {lineno}: finbert_gate must be on|off, got {val!r}")
            gate = (v == "on")
        else:
            raise ValueError(f"line {lineno}: unknown key {key!r}")
    return life, gate


def load_trigger_conditions(path) -> TriggerConditions:
    """Parse the trigger-conditions file. Missing file -> WARN + built-ins;
    any malformed content -> ERROR + built-ins (never a partial config)."""
    builtin = TriggerConditions.builtin()
    if not os.path.exists(path):
        logger.warning("trigger-conditions file %s not found — using built-ins "
                       "(lifeTime %s, finbert_gate on)", path, MOLE_LIFETIME_STR)
        return builtin
    try:
        have_default = False
        default_life, default_gate = None, None
        windows = []
        with open(path, encoding="utf-8") as f:
            for lineno, raw in enumerate(f, 1):
                line = raw.split("#", 1)[0].strip()
                if not line:
                    continue
                tokens = line.split()
                kind = tokens[0]
                if kind == "DEFAULT":
                    if have_default:
                        raise ValueError(f"line {lineno}: duplicate DEFAULT line")
                    have_default = True
                    default_life, default_gate = \
                        _parse_condition_keys(tokens[1:], lineno)
                elif kind == "WINDOW":
                    if len(tokens) < 2 or "-" not in tokens[1]:
                        raise ValueError(
                            f"line {lineno}: WINDOW needs a HH:MM-HH:MM span")
                    span = tokens[1]
                    s_str, _, e_str = span.partition("-")
                    start = xwing.parse_hhmm(s_str)
                    end = xwing.parse_hhmm(e_str)
                    if start >= end:
                        raise ValueError(
                            f"line {lineno}: window start must be before end "
                            f"(midnight-crossing not supported), got {span!r}")
                    life, gate = _parse_condition_keys(tokens[2:], lineno)
                    windows.append(Window(start, end, life, gate, span))
                else:
                    raise ValueError(
                        f"line {lineno}: expected DEFAULT or WINDOW, got {kind!r}")
        for i, a in enumerate(windows):
            for b in windows[i + 1:]:
                if a.start < b.end and b.start < a.end:
                    raise ValueError(f"windows {a.label} and {b.label} overlap")
    except Exception as e:
        logger.error("trigger-conditions file %s is malformed (%s) — using "
                     "built-ins (lifeTime %s, finbert_gate on)",
                     path, e, MOLE_LIFETIME_STR)
        return builtin

    if not have_default:
        logger.warning("trigger-conditions %s has no DEFAULT line — built-ins "
                       "(lifeTime %s, finbert_gate on) fill the default regime",
                       path, MOLE_LIFETIME_STR)
    cond = TriggerConditions(
        default_life if default_life is not None else builtin.default_lifetime_s,
        default_gate if default_gate is not None else builtin.default_finbert_gate,
        tuple(windows), "file")
    # Echo the effective config so the regime in force is always in the log.
    logger.info("trigger-conditions (%s): DEFAULT lifeTime=%ds finbert_gate=%s",
                path, cond.default_lifetime_s,
                "on" if cond.default_finbert_gate else "off")
    for w in cond.windows:
        life = w.lifetime_s if w.lifetime_s is not None else cond.default_lifetime_s
        gate = w.finbert_gate if w.finbert_gate is not None \
            else cond.default_finbert_gate
        logger.info("trigger-conditions: WINDOW %s ET (end exclusive) "
                    "lifeTime=%ds finbert_gate=%s",
                    w.label, life, "on" if gate else "off")
    return cond


# --------------------------------------------------------------------------- #
# Shared ibapi client (one socket; both market data AND orders)
# --------------------------------------------------------------------------- #
class SharedIBClient(EWrapper, EClient):
    """A single warm connection. While a TrioSession is bound, fan tick callbacks
    to the trade-mole detector, the CURRENT x-wing controller (session.xwing —
    T47 swaps it per relaunch) and T47, and route order callbacks to x-wing with
    a T47 echo. Owns the monotonic order-id stream for the connection."""

    def __init__(self, client_id: int, host: str, port: int):
        EClient.__init__(self, self)
        self.client_id = client_id
        self.host = host
        self.port = port

        self.connected_evt = threading.Event()
        self.disconnected_flag = False

        self._id_lock = threading.Lock()
        self.next_order_id = None

        # contract-resolution handshake (used by TrioSession._begin_collection)
        self.contract_evt = threading.Event()
        self._contract_details = None

        # currently bound trio session (None when idle/free)
        self.session = None

    # ---- order-id allocation ----
    def alloc_order_id(self):
        with self._id_lock:
            oid = self.next_order_id
            self.next_order_id += 1
            return oid

    # ---- connection / ids ----
    def nextValidId(self, orderId: int):
        with self._id_lock:
            if self.next_order_id is None or orderId > self.next_order_id:
                self.next_order_id = orderId
        if not self.connected_evt.is_set():
            logger.info("client %d connected; nextValidId=%d", self.client_id, orderId)
            self.connected_evt.set()

    def connectionClosed(self):
        self.disconnected_flag = True
        logger.warning("client %d connection closed", self.client_id)

    # ---- contract resolution ----
    def contractDetails(self, reqId, contractDetails):
        self._contract_details = contractDetails
        self.contract_evt.set()

    def contractDetailsEnd(self, reqId):
        self.contract_evt.set()

    # ---- errors ----
    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        info = {2104, 2106, 2107, 2108, 2109, 2119, 2158, 2100, 2150, 399,
                10167, 10197}
        msg = f"client {self.client_id} reqId={reqId} code={errorCode}: {errorString}"
        if errorCode in info:
            logger.info(msg)
        else:
            logger.warning(msg)
        s = self.session
        if s is not None:
            try:
                s.xwing.on_order_error(reqId, errorCode, errorString)
            except Exception as e:
                logger.error("on_order_error routing failed: %s", e, exc_info=True)

    # ---- order callbacks -> x-wing ----
    def orderStatus(self, orderId, status, filled, remaining, avgFillPrice,
                    permId, parentId, lastFillPrice, clientId, whyHeld,
                    mktCapPrice=0.0):
        s = self.session
        if s is not None:
            s.xwing.on_order_status(orderId, status, float(filled),
                                    float(avgFillPrice), float(lastFillPrice),
                                    why_held=whyHeld or "")
            # T47 echo AFTER x-wing processed it (same thread, so reading the
            # controller's orders dict is race-free): stamps ENTRY_FILL /
            # EXIT_FILL rows, captures EP/SP, enforces the late-fill guard.
            try:
                s.t47.observe_order_status(orderId, status, float(filled),
                                           float(avgFillPrice),
                                           float(lastFillPrice))
            except Exception as e:
                logger.error("t47 observe_order_status failed: %s", e,
                             exc_info=True)

    def openOrder(self, orderId, contract, order, orderState):
        # INFO, not DEBUG: on a silent "Inactive" entry (rejected-orders-findings
        # PART B) warningText/completedStatus is the only reason IBKR ever sends.
        logger.info("client %d openOrder %d %s %s status=%s completed=%s warn=%r",
                    self.client_id, orderId, order.action, order.orderType,
                    orderState.status,
                    getattr(orderState, "completedStatus", ""),
                    getattr(orderState, "warningText", "") or "")

    def execDetails(self, reqId, contract, execution):
        logger.debug("client %d exec %s %s @ %.4f", self.client_id,
                     execution.side, execution.shares, execution.price)

    # ---- market-data callbacks -> fan out to BOTH ----
    def tickPrice(self, reqId, tickType, price, attrib):
        s = self.session
        if s is None:
            return
        s.mole.tickPrice(reqId, tickType, price, attrib)
        xwing.route_tick_price(s.xwing, tickType, price)
        # T47 last: on the surge tick x-wing's quote is already updated before
        # any T47 row is written. Guarded so a T47 bug can't kill the fan-out.
        try:
            s.t47.on_tick_price(tickType, price)
        except Exception as e:
            logger.error("t47 on_tick_price failed: %s", e, exc_info=True)

    def tickSize(self, reqId, tickType, size):
        s = self.session
        if s is not None:
            s.mole.tickSize(reqId, tickType, size)

    def tickString(self, reqId, tickType, value):
        s = self.session
        if s is not None:
            s.mole.tickString(reqId, tickType, value)

    def tickGeneric(self, reqId, tickType, value):
        s = self.session
        if s is not None:
            s.mole.tickGeneric(reqId, tickType, value)


# --------------------------------------------------------------------------- #
# Trio session: one trade-mole + one T47 + a SEQUENCE of x-wing runs on one
# shared client. After run 1, T47 owns the lifecycle: it swaps self.xwing per
# relaunch and decides when the trio ends (finish is only called at trio end).
# --------------------------------------------------------------------------- #
class TrioSession:
    def __init__(self, clerk, client: SharedIBClient, ticker: str,
                 last_close, iti_baseline, trade_size_baseline=None):
        self.clerk = clerk
        self.client = client
        self.symbol = ticker.upper()
        self.last_close = last_close
        # raw (pre-defaulting) payload values, persisted to the durable store so
        # replay reconstructs the session through this same constructor path.
        self._raw_iti_baseline = iti_baseline
        self._raw_trade_size_baseline = trade_size_baseline
        self.iti_baseline = iti_baseline if (iti_baseline and iti_baseline > 0) \
            else DEFAULT_ITI_BASELINE
        # Trade-size baseline (M9). The 44444 sentinel is passed through as-is;
        # trade-mole treats it (and any <=0) as "no baseline" and emits the M9
        # columns as None.
        self.trade_size_baseline = trade_size_baseline \
            if (trade_size_baseline and trade_size_baseline > 0) \
            else DEFAULT_TRADE_SIZE_BASELINE

        self._lock = threading.Lock()
        self._buy_fired = False
        self._finished = False
        self._mole_written = False    # guard: write the mole CSV exactly once
        self._watch_timer = None
        self._defer_timer = None     # closed-market: idle-until-04:00 ET timer
        self._defer_target = None    # target datetime (ET) we are deferring to
        self._sentiment_ok = threading.Event()
        self._sentiment_bypassed = False  # buy-signal fired with finbert_gate off
        # Set once the resolved contract (conId/minTick) has been applied to
        # x-wing. reqMktData rides a bare STK/SMART/USD contract and no longer
        # blocks on contract resolution; the entry order waits on THIS instead,
        # so it never prices/places against a null contract or the default
        # min_tick (0.01). See _begin_collection / _resolve_contract_async.
        self._contract_ready = threading.Event()
        self._handlers = []          # logging handlers to detach on finish
        self.output_path = trademole.driven_output_path(MOLE_OUTPUT_DIR, self.symbol)

        # --- per-duo loggers (distinct so concurrent duos don't cross-write) ---
        self.mole_log = self._make_mole_logger()
        self.xwing_log = xwing.setup_logging(
            xwing.LOG_DIR, self.symbol, "INFO",
            name=f"xwing.{self.symbol}.{client.client_id}", console=False)
        self._handlers += [h for h in self.xwing_log.handlers
                           if isinstance(h, logging.FileHandler)]

        # --- trade-mole (driven) ---
        self.mole = trademole.IBKRSurgeApp(
            self.symbol,
            baseline_avg_iti=self.iti_baseline,
            surge_dollar_rate=trademole.DEFAULT_SURGE_DOLLAR_RATE,
            surge_max_spread_pct=trademole.DEFAULT_SURGE_MAX_SPREAD_PCT,
            surge_min_lift_ratio=trademole.DEFAULT_SURGE_MIN_LIFT_RATIO,
            surge_min_bid_drift_bp=trademole.DEFAULT_SURGE_MIN_BID_DRIFT_BP,
            surge_min_trades_5s=trademole.DEFAULT_SURGE_MIN_TRADES_5S,
            surge_max_price_drop_pct=trademole.DEFAULT_SURGE_MAX_PRICE_DROP_PCT,
            baseline_avg_trade_size=self.trade_size_baseline,
            large_trade_mult=trademole.DEFAULT_LARGE_TRADE_MULT,
            pretrade_staleness_fire=trademole.PRETRADE_STALENESS_FIRE,
            pretrade_staleness_threshold=trademole.PRETRADE_STALENESS_THRESHOLD,
            logger=self.mole_log,
        )
        self.mole.stop_at_trigger = True
        self.mole.buy_signal_callback = self.on_buy_signal

        # --- x-wing (driven; run 1's controller — T47 swaps in fresh ones) ---
        self.xwing = xwing.build_driven(
            self.symbol, self.last_close, self.client,
            log=self.xwing_log, on_done=self._on_xwing_done,
            id_alloc=self.client.alloc_order_id,
        )

        # --- T47 (re-entry controller; owns the trio lifecycle post-trigger) ---
        # The resolved ContractDetails is cached in self._cd ONCE by
        # _resolve_contract_async; every relaunched x-wing gets it applied via
        # build_new_xwing — the shared per-client reqId-98 slot is never
        # re-requested mid-session (see the CLAUDE.md warning).
        self._cd = None
        self.t47 = t47mod.T47Controller(self.symbol, self, log=self.xwing_log)

    # ------------------------------------------------------------------ #
    def _make_mole_logger(self):
        os.makedirs(MOLE_LOG_DIR, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        path = os.path.join(MOLE_LOG_DIR, f"trade-mole_{self.symbol}_{ts}.log")
        lg = logging.getLogger(f"trade-mole.{self.symbol}.{self.client.client_id}")
        lg.setLevel(logging.INFO)
        fmt = logging.Formatter("%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
                                datefmt="%H:%M:%S")
        fh = logging.FileHandler(path)
        fh.setFormatter(fmt)
        lg.addHandler(fh)
        self._handlers.append(fh)
        lg.info("Log file: %s", path)
        return lg

    def start(self):
        """Bind to the client and either begin collection now (market open) or,
        for a closed-market trigger, keep the client bound and idle until 04:00
        ET before subscribing. Returns True on success (accepted)."""
        self.client.session = self

        # Closed-market window? Reuse trade-mole's pure clock helper so the ET
        # open-gate logic lives in exactly one place.
        delay_sec, target_et = trademole._compute_collection_start_delay()
        if delay_sec > 0:
            self._defer_target = target_et
            self._defer_timer = threading.Timer(delay_sec,
                                                 self._begin_collection_safe)
            self._defer_timer.daemon = True
            self._defer_timer.start()
            # Persist so this deferred duo survives a clerk crash/restart and is
            # re-armed at startup (removed once collection begins or it finishes).
            self.clerk.store.upsert(self._to_record())
            logger.info("[%s] closed-market trigger: client %d bound, deferring "
                        "subscribe %.0fs until %s ET", self.symbol,
                        self.client.client_id, delay_sec,
                        target_et.isoformat() if target_et else "04:00")
            return True

        return self._begin_collection()

    def conditions_instant(self) -> datetime:
        """The ET instant whose window governs this trio's trigger regime.

        For a trio still deferred by a closed-market arm, that is its scheduled
        collection start (04:00 ET) — NOT now, which may be hours earlier and in
        a different regime. _begin_collection already anchors the watch lifetime
        on collection start for exactly this reason, and on_buy_signal resolves
        finbert_gate at the surge instant; a deferred trio's surge can only ever
        happen in its collection window, so that is the window that must decide
        whether sentiment has any authority over it.

        Once collection has begun (_watch_timer armed), `now` is correct.
        """
        if self._defer_target is not None and self._watch_timer is None:
            return self._defer_target
        return datetime.now(tz=trademole.ET)

    def _to_record(self) -> dict:
        """Serialize the fields needed to reconstruct this deferred duo on replay."""
        return {
            "symbol": self.symbol,
            "last_close": self.last_close,
            "iti_baseline": self._raw_iti_baseline,
            "trade_size_baseline": self._raw_trade_size_baseline,
            "defer_target_et": self._defer_target.isoformat()
                if self._defer_target is not None else None,
            "sentiment": "ok" if self._sentiment_ok.is_set() else "pending",
            "created_at": datetime.now(tz=trademole.ET).isoformat(),
        }

    def set_sentiment_ok(self):
        self._sentiment_ok.set()
        # Upgrade the durable record if this duo is still deferred (no-op once it
        # has begun collecting and removed its record, or for open-market duos).
        self.clerk.store.set_sentiment(self.symbol, "ok")

    def _begin_collection_safe(self):
        """Defer-timer entry point (runs on the Timer thread, off the request
        path). Releases the client cleanly if collection cannot be started."""
        try:
            if not self._begin_collection():
                self.finish("deferred_start_failed")
        except Exception as e:
            logger.error("[%s] deferred start raised on client %d: %s",
                         self.symbol, self.client.client_id, e, exc_info=True)
            self.finish("deferred_start_error")

    def _begin_collection(self):
        """Resolve the contract, subscribe to market data, and arm the watch
        timer. Reused by the open-market path and the deferred (04:00) path.
        Returns True on success."""
        # A bound client is skipped by the reconnection watchdog, so after an
        # overnight Gateway restart the socket is likely dead -- reconnect it
        # ourselves before subscribing.
        if self.clerk._down(self.client):
            logger.info("[%s] bound client %d is down at collection start; "
                        "reconnecting", self.symbol, self.client.client_id)
            if not self.clerk._reconnect_bound(self.client):
                logger.error("[%s] could not reconnect client %d for collection",
                             self.symbol, self.client.client_id)
                return False

        # Fire contract resolution but DO NOT block on it. Market data does not
        # need a qualified contract -- IBKR resolves a bare STK/SMART/USD contract
        # server-side and streams the same quote -- so subscribe immediately and
        # resolve the conId/minTick in the background (_resolve_contract_async).
        # This takes the ~hundreds-of-ms reqContractDetails round-trip off the
        # reqMktData critical path (the Ask price is what prices the entry). Only
        # the LATER entry order needs the resolved contract; it waits on
        # self._contract_ready.
        self.client.contract_evt.clear()
        self.client._contract_details = None
        self.client.reqContractDetails(CD_REQ_ID, xwing.make_contract(self.symbol))

        # one market-data line feeds BOTH (mole's generic ticks superset) -- on the
        # bare contract, so the Ask starts flowing now instead of ~560 ms later.
        self.client.reqMarketDataType(1)
        self.client.reqMktData(MKT_REQ_ID, xwing.make_contract(self.symbol),
                               trademole.GENERIC_TICKS, False, False, [])

        # Apply the resolved contract to x-wing off the hot path; the entry order
        # gates on self._contract_ready so we never trade an unresolved contract.
        threading.Thread(target=self._resolve_contract_async, daemon=True,
                         name=f"resolve-{self.symbol}").start()

        # Watch lifetime is resolved against the ET clock NOW (collection start,
        # which for deferred overnight duos is 04:00 ET, not trigger arrival).
        # The finbert_gate shown here is informational only — it is re-evaluated
        # at the surge instant in on_buy_signal.
        res = self.clerk.conditions.at(datetime.now(tz=trademole.ET))
        watch = res.lifetime_s
        self._watch_timer = threading.Timer(watch, self._on_watch_timeout)
        self._watch_timer.daemon = True
        self._watch_timer.start()
        # T47's watch-lifetime clock anchors HERE (04:00 ET for deferred
        # overnight sessions, not trigger arrival) — same anchor as the mole's
        # watch timer above.
        self.t47.set_anchor()
        # Collection has begun: the market is open and the duo no longer needs
        # crash-replay. Drop its durable record (no-op for the open-market path,
        # which never created one).
        self.clerk.store.remove(self.symbol)
        logger.info("[%s] trio armed on client %d (watch %ds [window %s, "
                    "finbert_gate=%s at start], iti_baseline=%.2f, "
                    "trade_size_baseline=%.2f, lastClose=%s)", self.symbol,
                    self.client.client_id, watch, res.window,
                    "on" if res.finbert_gate else "off", self.iti_baseline,
                    self.trade_size_baseline, self.last_close)
        return True

    # ------------------------------------------------------------------ #
    def _resolve_contract_async(self):
        """Background contract resolver, run off the reqMktData hot path. Waits
        for the reqContractDetails reply fired in _begin_collection, applies the
        resolved contract (+ minTick) to x-wing so the entry order uses the exact
        conId, then signals self._contract_ready. On failure (timeout, or a
        contractDetailsEnd with no contractDetails -> unknown symbol) it aborts
        the duo, preserving the old inline guarantee that we never trade on an
        unresolved contract -- just deferred off the hot path."""
        if self.client.contract_evt.wait(timeout=15) and \
                self.client._contract_details is not None:
            # Cache session-side FIRST: relaunched x-wings are built from this
            # copy (build_new_xwing), never from the shared per-client slot.
            self._cd = self.client._contract_details
            self.xwing.set_contract_details(self._cd)
            self._contract_ready.set()
        else:
            logger.error("[%s] contract resolution failed on client %d — "
                         "aborting duo (no order placed)",
                         self.symbol, self.client.client_id)
            self.finish("contract_unresolved")

    # ------------------------------------------------------------------ #
    def on_buy_signal(self, last_ask, trigger_print=None):
        """Called by trade-mole on the ibapi thread when the surge fires.
        Cancels the watch window, evaluates finbert_gate at THIS instant, and
        spawns a sentinel thread that either waits for the Sentiment gate or
        (gate off) hands off to x-wing immediately. `trigger_print` is the surge
        trade's print, threaded through to x-wing so the entry is priced off the
        higher of it and the (often stale) ask."""
        res = self.clerk.conditions.at(datetime.now(tz=trademole.ET))
        with self._lock:
            if self._buy_fired or self._finished:
                return
            self._buy_fired = True
            if not res.finbert_gate:
                # Set BEFORE the launch thread starts so a racing sentiment
                # message can never observe fired-but-unflagged.
                self._sentiment_bypassed = True
            if self._watch_timer is not None:
                self._watch_timer.cancel()
        if res.finbert_gate:
            logger.info("[%s] BUY-SIGNAL (last_ask=%s print=%s) on client %d — awaiting sentiment gate",
                        self.symbol, last_ask, trigger_print, self.client.client_id)
        else:
            logger.info("[%s] BUY-SIGNAL (last_ask=%s print=%s) on client %d — finbert_gate "
                        "OFF (window %s): bypassing sentiment gate",
                        self.symbol, last_ask, trigger_print, self.client.client_id, res.window)
        t = threading.Thread(target=self._await_sentiment_and_launch,
                             args=(last_ask, trigger_print, res.finbert_gate),
                             name=f"sentiment-gate-{self.symbol}", daemon=True)
        t.start()

    def _write_mole_once(self):
        """Write the collected trade-mole dataset exactly once. Safe to call
        from the trigger path and again from finish(): the flag guarantees a
        single write. The mole halts recording at the buy-signal, so the
        record buffer is already final by the time the trigger fires."""
        with self._lock:
            if self._mole_written:
                return
            self._mole_written = True
        try:
            trademole.write_records_output(self.mole, self.output_path,
                                           log=self.mole_log)
        except Exception as e:
            logger.error("[%s] writing trade-mole output failed: %s",
                         self.symbol, e, exc_info=True)

    def _await_sentiment_and_launch(self, last_ask, trigger_print, gate_on):
        """Sentinel thread: waits for the Sentiment gate then launches x-wing,
        or aborts the trade on timeout. With gate_on=False (finbert_gate off at
        the surge instant) the wait is skipped and x-wing launches immediately."""
        # The mole has stopped recording at the trigger; persist its table now
        # (off the ibapi thread) so the dataset survives regardless of whether
        # the sentiment gate passes or x-wing ever fills.
        self._write_mole_once()
        if gate_on:
            confirmed = self._sentiment_ok.wait(timeout=SENTIMENT_TIMEOUT_S)
            if self._finished:
                return
            if not confirmed:
                logger.warning("[%s] sentiment gate timed out (%.1fs) — aborting trade",
                               self.symbol, SENTIMENT_TIMEOUT_S)
                self.finish("sentiment_timeout")
                return
            logger.info("[%s] sentiment gate confirmed — launching x-wing (last_ask=%s)",
                        self.symbol, last_ask)
        else:
            if self._finished:
                return
            logger.info("[%s] launching x-wing without sentiment gate (last_ask=%s)",
                        self.symbol, last_ask)
        # The contract resolves in parallel with market data; the entry MUST NOT
        # price/place before it lands (else null contract / default min_tick). By
        # surge+sentiment time it is essentially always set, so this returns at
        # once; it only bites if resolution is still pending or has failed.
        if self._finished or not self._contract_ready.wait(timeout=15):
            if not self._finished:
                logger.error("[%s] contract not resolved at launch — aborting trade",
                             self.symbol)
                self.finish("contract_unresolved")
            return
        # Hand off to T47: it books run 1 (state machine, heartbeat, TSV row)
        # then calls xwing.on_buy_signal + starts the run thread with today's
        # pricing args untouched.
        self.t47.launch_run1(last_ask, trigger_print)

    def _on_watch_timeout(self):
        with self._lock:
            if self._buy_fired or self._finished:
                return
        logger.info("[%s] watch window elapsed with no surge; releasing client %d",
                    self.symbol, self.client.client_id)
        self.finish("no_surge")

    def _on_xwing_done(self, reason):
        # x-wing's run_trade has already flattened + finalized its own TSV.
        # T47 decides what happens next: resume the WATCH (and maybe relaunch
        # a fresh x-wing) or end the trio via finish("t47:...").
        self.t47.on_run_done(reason)

    # ------------------------------------------------------------------ #
    def finish(self, reason):
        """Idempotent teardown: stop market data, write the trade-mole CSV,
        detach loggers, reset the client and return it to the free pool. The
        socket is NEVER dropped."""
        with self._lock:
            if self._finished:
                return
            self._finished = True
        self._sentiment_ok.set()                    # unblock sentinel if waiting
        self.clerk.unregister_session(self.symbol)  # remove from routing table
        self.clerk.store.remove(self.symbol)        # drop durable record (catch-all)
        logger.info("[%s] finishing trio on client %d (%s)",
                    self.symbol, self.client.client_id, reason)
        # T47 first (idempotent): stops its heartbeat, stamps the final DONE
        # row and closes its TSV — before market data is cancelled below. A
        # session that never launched run 1 has no T47 file; nothing is written.
        self.t47.shutdown(reason)

        # Cancel a still-pending closed-market defer timer (e.g. shutdown or an
        # early release before 04:00) so it can't fire after teardown.
        if self._defer_timer is not None:
            self._defer_timer.cancel()

        try:
            self.client.cancelMktData(MKT_REQ_ID)
        except Exception as e:
            logger.debug("cancelMktData error: %s", e)

        # write the collected dataset (pre-trigger window + trigger row).
        # Idempotent: a triggered duo already wrote it at trigger time; this
        # covers the non-triggered exits (no_surge, sentiment_timeout/bad).
        self._write_mole_once()

        # detach per-duo log handlers so loggers don't accumulate across reuse
        for lg in (self.mole_log, self.xwing_log):
            for h in list(lg.handlers):
                if h in self._handlers:
                    lg.removeHandler(h)
                    try:
                        h.close()
                    except Exception:
                        pass

        self.client.session = None
        self.clerk.release_client(self.client)

    # ------------------------------------------------------------------ #
    # T47 façade (the session side of the trio contract)
    # ------------------------------------------------------------------ #
    def build_new_xwing(self):
        """Fresh controller for a T47 relaunch: SAME shared client and order-id
        stream, cached resolved contract applied (no new reqContractDetails —
        the shared per-client reqId-98 slot stays untouched). Each controller
        brings its own PriceActionWriter, so every run gets its own
        xwing-table TSV."""
        if self._cd is None:
            raise RuntimeError("contract details not resolved; cannot relaunch")
        ctl = xwing.build_driven(
            self.symbol, self.last_close, self.client,
            log=self.xwing_log, on_done=self._on_xwing_done,
            id_alloc=self.client.alloc_order_id,
        )
        ctl.set_contract_details(self._cd)
        return ctl

    def start_run_thread(self, ctl, run_no):
        threading.Thread(target=xwing.run_trade, args=(ctl, time.time()),
                         name=f"xwing-{self.symbol}-r{run_no}",
                         daemon=True).start()

    def client_dead(self) -> bool:
        """T47 heartbeat probe (keeps the T47 module ibapi-free)."""
        return self.clerk._down(self.client)


# --------------------------------------------------------------------------- #
# Durable pending-duo store
# --------------------------------------------------------------------------- #
class PendingStore:
    """A crash-durable snapshot of the deferred (closed-market) duos that are
    waiting for the next market open. Scope is deliberately narrow: only duos
    that have NOT yet begun collecting market data are tracked, so a clerk
    restart re-arms them. Open-market in-flight duos (which may hold live x-wing
    orders) are NOT tracked -- resuming those needs IBKR order reconciliation.

    Storage is one JSON snapshot file, rewritten atomically on every mutation
    (temp file + fsync + os.replace). The in-memory `_data` dict is the source of
    truth; the file is just its serialization. Volume is tens of records at most,
    so a full rewrite per mutation is cheap and avoids any log-replay semantics.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data: dict[str, dict] = {}

    def load(self):
        """Read the snapshot on startup. A missing/empty/corrupt file is treated
        as an empty store (logged) -- a bad journal must never crash the clerk."""
        with self._lock:
            try:
                raw = self.path.read_text(encoding="utf-8")
            except FileNotFoundError:
                self._data = {}
                return
            except Exception as e:
                logger.warning("pending store unreadable (%s); starting empty", e)
                self._data = {}
                return
            try:
                doc = json.loads(raw) if raw.strip() else {}
                self._data = dict(doc.get("duos", {}))
            except Exception as e:
                logger.warning("pending store corrupt (%s); starting empty", e)
                self._data = {}

    def records(self) -> list[dict]:
        """Snapshot copy of the current records (for replay)."""
        with self._lock:
            return [dict(v) for v in self._data.values()]

    def upsert(self, record: dict):
        with self._lock:
            self._data[record["symbol"]] = dict(record)
            self._flush_locked()

    def set_sentiment(self, symbol: str, value: str):
        """Upgrade a record's sentiment in place. No-op (no write) if the symbol
        is not tracked -- open-market duos have no record."""
        with self._lock:
            rec = self._data.get(symbol)
            if rec is None:
                return
            rec["sentiment"] = value
            self._flush_locked()

    def remove(self, symbol: str):
        """Drop a record. No-op (no write) if the symbol is not tracked."""
        with self._lock:
            if symbol not in self._data:
                return
            del self._data[symbol]
            self._flush_locked()

    def _flush_locked(self):
        """Atomically rewrite the snapshot. Caller holds self._lock."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        doc = {"version": 1, "duos": self._data}
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(
                    "w", dir=str(self.path.parent), prefix=".pending_duos.",
                    suffix=".tmp", delete=False, encoding="utf-8") as f:
                tmp = f.name
                json.dump(doc, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except Exception as e:
            logger.error("pending store flush failed: %s", e, exc_info=True)
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass


# --------------------------------------------------------------------------- #
# Client pool
# --------------------------------------------------------------------------- #
class ClientPool:
    def __init__(self):
        self._lock = threading.Lock()
        self._free = deque()
        self._all = []

    def add(self, client):
        self._all.append(client)
        self._free.append(client)

    def acquire(self):
        with self._lock:
            if self._free:
                return self._free.popleft()
            return None

    def release(self, client):
        with self._lock:
            if client not in self._free:
                self._free.append(client)

    def remove_if_free(self, client) -> bool:
        """Atomically claim an idle client (one in the free pool) for the
        watchdog so it cannot race handle_trigger's acquire(). A BUSY client is
        not in _free, so this returns False and the watchdog skips it."""
        with self._lock:
            if client in self._free:
                self._free.remove(client)
                return True
            return False

    @property
    def free_count(self):
        with self._lock:
            return len(self._free)

    @property
    def all_clients(self):
        return list(self._all)


# --------------------------------------------------------------------------- #
# Clerk
# --------------------------------------------------------------------------- #
class Clerk:
    def __init__(self, host, port, client_qty, listen_host, listen_port,
                 watchdog_interval=WATCHDOG_INTERVAL_S, conditions=None):
        self.host = host
        self.port = port
        self.client_qty = client_qty
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.watchdog_interval = watchdog_interval
        self.conditions = conditions or TriggerConditions.builtin()
        self.pool = ClientPool()
        self._stop = threading.Event()
        self._server_sock = None
        self._watchdog_thread = None
        self._sessions: dict[str, "TrioSession"] = {}  # ticker → active TrioSession
        self._sessions_lock = threading.Lock()
        # durable queue of deferred duos (survives restarts); loaded here,
        # replayed in replay_pending() after the pool is warm.
        self.store = PendingStore(HERE / CLERK_STATE_DIR / PENDING_DUOS_FILE)
        self.store.load()

    # ---- pool lifecycle ----
    def _connect_one(self, client, *, timeout=15) -> bool:
        """(Re)connect an existing SharedIBClient on its own clientId. Resets the
        per-connection state (so a fresh nextValidId reseeds the order-id stream
        cleanly), opens a new socket, starts a fresh run() thread, and waits for
        nextValidId. Reused by startup AND the reconnection watchdog. Returns
        True on success."""
        cid = client.client_id
        client.connected_evt.clear()
        client.disconnected_flag = False
        with client._id_lock:
            client.next_order_id = None     # force reseed from the fresh nextValidId
        try:
            if client.isConnected():
                client.disconnect()
        except Exception:
            pass
        logger.info("connecting client %d to %s:%d ...", cid, self.host, self.port)
        client.connect(self.host, self.port, cid)
        threading.Thread(target=client.run, daemon=True,
                         name=f"ibapi-{cid}").start()
        if not client.connected_evt.wait(timeout=timeout):
            logger.warning("client %d did not connect within %ds", cid, timeout)
            return False
        return True

    def connect_pool(self):
        for i in range(self.client_qty):
            cid = CLIENT_ID_START + i
            c = SharedIBClient(cid, self.host, self.port)
            if not self._connect_one(c):
                raise RuntimeError(f"client {cid} did not connect within 15s")
            self.pool.add(c)
        logger.info("pool ready: %d warm clients (ids %d..%d)", self.client_qty,
                    CLIENT_ID_START, CLIENT_ID_START + self.client_qty - 1)

    # ---- reconnection watchdog ----
    @staticmethod
    def _down(client) -> bool:
        if client.disconnected_flag:
            return True
        try:
            return not client.isConnected()
        except Exception:
            return True

    def _reconnect_idle(self, client):
        """Reconnect a single idle client that the watchdog has claimed via
        remove_if_free(). Retries with backoff to cover the ~1-2 min the Gateway
        is down during its daily restart, then ALWAYS returns the client to the
        free pool (warm on success; still-down and retried next tick on failure
        -- handle_trigger tolerates acquiring a still-dead client)."""
        cid = client.client_id
        ok = False
        for _ in range(MAX_RECONNECT_ATTEMPTS):
            if self._stop.is_set():
                break
            if self._connect_one(client, timeout=RECONNECT_CONNECT_TIMEOUT):
                ok = True
                break
            self._stop.wait(RECONNECT_BACKOFF_S)
        self.pool.release(client)
        if ok:
            logger.info("client %d reconnected (free=%d)", cid, self.pool.free_count)
        else:
            logger.warning("client %d still down; will retry next watchdog tick", cid)

    def _reconnect_bound(self, client) -> bool:
        """Reconnect a client that is BUSY (bound to a deferred session) and so
        is skipped by the watchdog. Same primitive/backoff as _reconnect_idle
        but never touches the pool -- the client must stay bound to its session.
        Returns True once reconnected, False if it stays down or we are stopping."""
        cid = client.client_id
        for _ in range(MAX_RECONNECT_ATTEMPTS):
            if self._stop.is_set():
                return False
            if self._connect_one(client, timeout=RECONNECT_CONNECT_TIMEOUT):
                logger.info("bound client %d reconnected", cid)
                return True
            self._stop.wait(RECONNECT_BACKOFF_S)
        return False

    def _watchdog_tick(self):
        for c in self.pool.all_clients:
            if not self._down(c):
                continue
            # Only touch idle (free) clients. A busy/mid-trade client fails
            # remove_if_free and is skipped; it is re-warmed after its session
            # ends and releases it back to the pool.
            if self.pool.remove_if_free(c):
                self._reconnect_idle(c)

    def _watchdog_loop(self):
        logger.info("reconnection watchdog started (interval=%ss)",
                    self.watchdog_interval)
        while not self._stop.wait(self.watchdog_interval):
            try:
                self._watchdog_tick()
            except Exception as e:
                logger.error("watchdog tick error: %s", e, exc_info=True)

    def start_watchdog(self):
        if self.watchdog_interval and self.watchdog_interval > 0:
            self._watchdog_thread = threading.Thread(
                target=self._watchdog_loop, daemon=True, name="reconnect-watchdog")
            self._watchdog_thread.start()
        else:
            logger.info("reconnection watchdog disabled (interval=%s)",
                        self.watchdog_interval)

    def register_session(self, session: "TrioSession"):
        with self._sessions_lock:
            self._sessions[session.symbol] = session

    def unregister_session(self, symbol: str):
        with self._sessions_lock:
            self._sessions.pop(symbol, None)

    def release_client(self, client):
        self.pool.release(client)
        logger.info("client %d returned to pool (free=%d)",
                    client.client_id, self.pool.free_count)

    def _acquire_healthy_client(self):
        """Pop free clients until we find a healthy one. Any disconnected client
        is released back (the watchdog re-warms it); the `tried` guard stops us
        spinning on the same dead clients. Returns the client or None if none are
        free/healthy. Shared by handle_trigger and replay_pending."""
        tried = []
        while True:
            c = self.pool.acquire()
            if c is None:
                return None
            if self._down(c):
                self.pool.release(c)
                if c in tried:        # cycled through all free clients; all dead
                    return None
                tried.append(c)
                continue
            return c

    # ---- trigger handling ----
    def handle_trigger(self, payload: dict) -> dict:
        ticker = payload.get("ticker")
        if not ticker:
            return {"status": "rejected", "reason": "missing 'ticker'"}
        last_close = payload.get("lastDailyClose")
        iti = payload.get("itiBaseline")
        trade_size = payload.get("tradeSizeBaseline")

        client = self._acquire_healthy_client()
        if client is None:
            logger.warning("[%s] no healthy free client; trigger rejected", ticker)
            return {"status": "rejected", "reason": "no_free_client"}

        session = TrioSession(self, client, ticker, last_close, iti, trade_size)
        self.register_session(session)
        try:
            ok = session.start()
        except Exception as e:
            logger.error("[%s] session start failed: %s", ticker, e, exc_info=True)
            ok = False
        if not ok:
            client.session = None
            self.release_client(client)
            return {"status": "rejected", "reason": "session_start_failed"}
        reply = {"status": "accepted", "clientId": client.client_id,
                 "symbol": session.symbol}
        if session._defer_target is not None:
            reply["deferredUntilET"] = session._defer_target.isoformat()
        return reply

    # ---- crash-recovery replay ----
    def replay_pending(self):
        """Re-arm deferred duos persisted before a crash/restart. Called at
        startup after the pool is warm and before serve(), so replayed sessions
        exist before any new trigger/sentiment arrives. A record whose defer
        target has already passed (e.g. crashed over a weekend, restarted after
        Monday's open) begins collecting immediately."""
        records = self.store.records()
        if not records:
            return
        logger.info("replaying %d persisted deferred duo(s)", len(records))
        for rec in records:
            symbol = rec.get("symbol")
            if not symbol:
                continue
            if rec.get("sentiment") == "bad":      # defensive; bad never persists
                self.store.remove(symbol)
                continue
            client = self._acquire_healthy_client()
            if client is None:
                # Leave the record on disk; a later restart with a warm pool can
                # pick it up. Stop trying -- no clients are available.
                logger.warning("[%s] no healthy free client for replay; left "
                               "queued", symbol)
                break

            session = TrioSession(self, client, symbol, rec.get("last_close"),
                                  rec.get("iti_baseline"),
                                  rec.get("trade_size_baseline"))
            self.register_session(session)
            if rec.get("sentiment") == "ok":
                session.set_sentiment_ok()
            else:
                logger.warning("[%s] replayed with sentiment still pending; if it "
                               "surges while finbert_gate is on, the sentiment "
                               "gate will time out (orchestrator does not re-send "
                               "sentiment)", symbol)

            target = None
            tgt_str = rec.get("defer_target_et")
            if tgt_str:
                try:
                    target = datetime.fromisoformat(tgt_str)
                except ValueError:
                    target = None
            now_et = datetime.now(tz=trademole.ET)
            delay = (target - now_et).total_seconds() if target is not None else 0.0

            if delay > 0:
                session._defer_target = target
                session._defer_timer = threading.Timer(
                    delay, session._begin_collection_safe)
                session._defer_timer.daemon = True
                session._defer_timer.start()
                logger.info("[%s] replayed: client %d bound, deferring subscribe "
                            "%.0fs until %s ET", symbol, client.client_id, delay,
                            target.isoformat())
            else:
                logger.info("[%s] replayed: defer target passed (%s) — beginning "
                            "collection now on client %d", symbol,
                            tgt_str or "n/a", client.client_id)
                threading.Thread(target=session._begin_collection_safe,
                                 name=f"replay-begin-{symbol}", daemon=True).start()

    # ---- TCP listener ----
    def serve(self):
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_sock.bind((self.listen_host, self.listen_port))
        self._server_sock.listen(16)
        self._server_sock.settimeout(1.0)
        logger.info("listening for orch-triggers on %s:%d",
                    self.listen_host, self.listen_port)
        while not self._stop.is_set():
            try:
                conn, addr = self._server_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle_conn, args=(conn, addr),
                             daemon=True).start()

    def _handle_conn(self, conn, addr):
        with conn:
            conn.settimeout(5.0)
            try:
                buf = b""
                while b"\n" not in buf and len(buf) < 65536:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                line = buf.split(b"\n", 1)[0].decode("utf-8").strip()
                if not line:
                    return
                payload = json.loads(line)
            except Exception as e:
                reply = {"status": "rejected", "reason": f"bad_request: {e}"}
                try:
                    conn.sendall((json.dumps(reply) + "\n").encode("utf-8"))
                except Exception:
                    pass
                return
            if "Sentiment" in payload:
                ticker = payload.get("ticker", "").upper()
                sentiment = payload.get("Sentiment")
                with self._sessions_lock:
                    session = self._sessions.get(ticker)
                if session and session._sentiment_bypassed:
                    # The duo already fired with finbert_gate off: sentiment has
                    # no authority over it — BAD must NOT kill the trade.
                    logger.info("[%s] Sentiment %s ignored: session fired with "
                                "finbert_gate off", ticker, sentiment)
                    reply = {"status": "sentiment_ignored_bypass", "symbol": ticker}
                elif session and sentiment == "OK":
                    session.set_sentiment_ok()
                    reply = {"status": "sentiment_ack", "symbol": ticker}
                elif session and sentiment == "BAD":
                    with session._lock:
                        fired = session._buy_fired
                    res = self.conditions.at(session.conditions_instant())
                    if fired:
                        # The buy-signal already fired: a run may be live or T47
                        # mid-WATCH, and a late BAD has no authority over it —
                        # it must NOT tear the trio down. (In 1.1 this exposure
                        # window was one run's duration; a trio lives for hours.
                        # A BAD landing inside the 2s gate wait is also caught
                        # here: the sentinel then times out unlaunched, so no
                        # trade happens either way.)
                        logger.info("[%s] Sentiment BAD ignored: trio already "
                                    "fired", ticker)
                        reply = {"status": "sentiment_ignored_late",
                                 "symbol": ticker}
                    elif not res.finbert_gate:
                        # Gate-off window: the surge is the only trigger
                        # authority, so a pre-surge BAD is discarded too. For a
                        # still-deferred trio the window is the one it will
                        # COLLECT in (04:00 ET), not the one this BAD arrived in.
                        logger.info("[%s] Sentiment BAD ignored: finbert_gate off "
                                    "in effective window (%s)%s", ticker, res.window,
                                    "" if session._defer_target is None
                                    else f" [deferred until {session._defer_target.isoformat()}]")
                        reply = {"status": "sentiment_ignored_gate_off",
                                 "symbol": ticker}
                    else:
                        # Pre-surge bad sentiment: tear the trio down now and
                        # return the warm client to the pool instead of holding
                        # it for the full watch window. finish() is idempotent.
                        session.finish("sentiment_bad")
                        reply = {"status": "sentiment_bad_released", "symbol": ticker}
                else:
                    reply = {"status": "sentiment_ignored", "symbol": ticker}
            else:
                reply = self.handle_trigger(payload)
            try:
                conn.sendall((json.dumps(reply) + "\n").encode("utf-8"))
            except Exception:
                pass

    def shutdown(self):
        self._stop.set()
        if self._watchdog_thread is not None:
            self._watchdog_thread.join(timeout=5)
        try:
            if self._server_sock is not None:
                self._server_sock.close()
        except Exception:
            pass
        for c in self.pool.all_clients:
            try:
                if c.isConnected():
                    c.disconnect()
            except Exception:
                pass
        logger.info("clerk shut down.")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def setup_logging(level: str):
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s.%(msecs)03d [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    os.makedirs(CLERK_LOG_DIR, exist_ok=True)
    # Daily log: today's entries go to clerk.log; at midnight it is rotated to
    # clerk.log.YYYY-MM-DD and a fresh clerk.log is started. Same-day restarts
    # append to the same file. Keep ~30 days of history.
    fh = TimedRotatingFileHandler(
        os.path.join(CLERK_LOG_DIR, "clerk.log"),
        when="midnight", backupCount=30, encoding="utf-8")
    fh.suffix = "%Y-%m-%d"
    fh.setFormatter(logging.Formatter(
        "%(asctime)s.%(msecs)03d [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S"))
    logging.getLogger().addHandler(fh)


def main():
    p = argparse.ArgumentParser(
        description="clerk-1.2: warm ibapi client pool running trade-mole + "
                    "T47 + x-wing trios on shared connections.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--client-qty", type=int, default=20,
                   help=f"Number of warm clients to connect (ids start at "
                        f"{CLIENT_ID_START}; max id {CLIENT_ID_MAX}).")
    p.add_argument("--host", default=n2_config.get(_CFG, "IBKR_GATEWAY_HOST", "127.0.0.1"),
                   help="IBKR Gateway/TWS host (default from n2_config_file.txt)")
    p.add_argument("--port", type=int,
                   default=n2_config.get_int(_CFG, "IBKR_GATEWAY_PORT", 4001),
                   help="IBKR port (4001 live GW / 4002 paper GW / 7497 paper TWS; "
                        "default from n2_config_file.txt)")
    p.add_argument("--listen-host", default=n2_config.get(_CFG, "CLERK_HOST", "127.0.0.1"),
                   help="Host/interface for the orch-trigger TCP socket "
                        "(default from n2_config_file.txt)")
    p.add_argument("--listen-port", type=int,
                   default=n2_config.get_int(_CFG, "CLERK_PORT", 8765),
                   help="TCP port for orch-triggers (JSON per line; "
                        "default from n2_config_file.txt)")
    p.add_argument("--trigger-conditions", default=TRIGGER_CONDITIONS_FILE,
                   help="Per-ET-window trigger-conditions file (lifeTime + "
                        "finbert_gate), loaded once at startup. Missing or "
                        "malformed -> built-ins (02:30, gate on).")
    p.add_argument("--watchdog-interval", type=float, default=WATCHDOG_INTERVAL_S,
                   help="Seconds between reconnection-watchdog polls that "
                        "re-warm pool clients after a Gateway restart (0 disables).")
    p.add_argument("--loglevel", default="INFO",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = p.parse_args()

    if args.client_qty < 1 or CLIENT_ID_START + args.client_qty - 1 > CLIENT_ID_MAX:
        p.error(f"--client-qty must be 1..{CLIENT_ID_MAX - CLIENT_ID_START + 1}")

    setup_logging(args.loglevel)
    if args.port in (4001, 7496):
        logger.warning("PORT %d looks like a LIVE trading port. Real money at risk.",
                       args.port)

    conditions = load_trigger_conditions(args.trigger_conditions)

    clerk = Clerk(args.host, args.port, args.client_qty,
                  args.listen_host, args.listen_port,
                  watchdog_interval=args.watchdog_interval,
                  conditions=conditions)

    def _sig(signum, frame):
        logger.info("signal %s received -> shutting down", signum)
        clerk.shutdown()
    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    try:
        clerk.connect_pool()
    except Exception as e:
        logger.error("pool connect failed: %s", e, exc_info=True)
        clerk.shutdown()
        sys.exit(1)

    clerk.replay_pending()
    clerk.start_watchdog()

    try:
        clerk.serve()
    except Exception as e:
        logger.error("server error: %s", e, exc_info=True)
    finally:
        clerk.shutdown()


if __name__ == "__main__":
    main()
