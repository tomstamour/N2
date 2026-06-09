#!/usr/bin/env python3
"""
clerk-1.1.py — orchestrator-facing client pool that runs trade-mole + x-wing
duos in-process on ONE shared ibapi connection each.

Why this exists
---------------
trade-mole-2.1 (surge detector) and x-wing-2.0 (trailing-stop trader) used to
open their own ibapi connections. The clerk keeps a pool of warm, pre-connected
clients (clientIds 1000..1000+N-1) and, on an orch-trigger, runs BOTH scripts on
ONE of those shared sockets:

    orchestrator ──TCP/JSON──► clerk
        {"ticker","lastDailyClose","itiBaseline","tradeSizeBaseline"}
    clerk picks a FREE warm client, then per client (a "duo"):
        SharedIBClient (one socket, one run() thread)
          ├─ tick callbacks ─► trade-mole (record + detect surge)
          │                 └► x-wing      (track quotes / entry ref)
          └─ order callbacks ► x-wing      (orderStatus / error / nextValidId)
        trade-mole surge ──► x-wing.on_buy_signal(last_ask)  (places orders)
        x-wing stop filled / lifetime ──► session.finish()

On completion the client is RESET (cancelMktData + clear state) and returned to
the pool WITHOUT dropping the socket, ready for the next trigger.

Startup
-------
    python3 clerk-1.1.py --client-qty 10 --port 4001 --listen-port 8765

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
import threading
import time
from collections import deque
from datetime import datetime
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

CLIENT_ID_START = 1000          # pool uses 1000 .. 1000 + qty - 1
CLIENT_ID_MAX = 1029            # inclusive ceiling (30 clients)
MKT_REQ_ID = 99                 # per-client market-data reqId (one duo at a time)
CD_REQ_ID = 98                  # per-client contractDetails reqId

# clerk's own daily log (one file per day, rolled at midnight)
CLERK_LOG_DIR = "clerk_logs"

# reconnection watchdog (re-warms pool clients after the daily Gateway restart)
WATCHDOG_INTERVAL_S       = 30   # poll period for the reconnection watchdog
RECONNECT_CONNECT_TIMEOUT = 10   # per-attempt wait for connected_evt
RECONNECT_BACKOFF_S       = 10   # sleep between reconnect attempts
MAX_RECONNECT_ATTEMPTS    = 18   # ~3 min of attempts per watchdog tick

# trade-mole driven-mode artifacts
MOLE_OUTPUT_DIR = "mole-outputs"
MOLE_LOG_DIR = "mole-logs"
MOLE_LIFETIME_STR = "01:00"     # watch window (mm:ss, per trade_mole convention)
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


xwing = _load_module("xwing2", "x-wing-2.0.py")
trademole = _load_module("trademole2", "trade-mole-2.1.py")


# --------------------------------------------------------------------------- #
# Shared ibapi client (one socket; both market data AND orders)
# --------------------------------------------------------------------------- #
class SharedIBClient(EWrapper, EClient):
    """A single warm connection. While a DuoSession is bound, fan tick callbacks
    to BOTH the trade-mole detector and the x-wing controller, and route order
    callbacks to x-wing. Owns the monotonic order-id stream for the connection."""

    def __init__(self, client_id: int, host: str, port: int):
        EClient.__init__(self, self)
        self.client_id = client_id
        self.host = host
        self.port = port

        self.connected_evt = threading.Event()
        self.disconnected_flag = False

        self._id_lock = threading.Lock()
        self.next_order_id = None

        # contract-resolution handshake (used by DuoSession.start)
        self.contract_evt = threading.Event()
        self._contract_details = None

        # currently bound duo (None when idle/free)
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
                                    float(avgFillPrice), float(lastFillPrice))

    def openOrder(self, orderId, contract, order, orderState):
        logger.debug("client %d openOrder %d %s %s", self.client_id, orderId,
                     order.action, orderState.status)

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
# Duo session: one trade-mole + one x-wing on one shared client
# --------------------------------------------------------------------------- #
class DuoSession:
    def __init__(self, clerk, client: SharedIBClient, ticker: str,
                 last_close, iti_baseline, trade_size_baseline=None):
        self.clerk = clerk
        self.client = client
        self.symbol = ticker.upper()
        self.last_close = last_close
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
        self._watch_timer = None
        self._defer_timer = None     # closed-market: idle-until-04:00 ET timer
        self._defer_target = None    # target datetime (ET) we are deferring to
        self._sentiment_ok = threading.Event()
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
            logger=self.mole_log,
        )
        self.mole.stop_at_trigger = True
        self.mole.buy_signal_callback = self.on_buy_signal

        # --- x-wing (driven) ---
        self.xwing = xwing.build_driven(
            self.symbol, self.last_close, self.client,
            log=self.xwing_log, on_done=self._on_xwing_done,
            id_alloc=self.client.alloc_order_id,
        )

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
            logger.info("[%s] closed-market trigger: client %d bound, deferring "
                        "subscribe %.0fs until %s ET", self.symbol,
                        self.client.client_id, delay_sec,
                        target_et.isoformat() if target_et else "04:00")
            return True

        return self._begin_collection()

    def set_sentiment_ok(self):
        self._sentiment_ok.set()

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

        # resolve contract (+ minTick) once on the shared client
        self.client.contract_evt.clear()
        self.client._contract_details = None
        self.client.reqContractDetails(CD_REQ_ID, xwing.make_contract(self.symbol))
        if not self.client.contract_evt.wait(timeout=15) or \
                self.client._contract_details is None:
            logger.error("[%s] contract resolution failed on client %d",
                         self.symbol, self.client.client_id)
            return False
        cd = self.client._contract_details
        self.xwing.set_contract_details(cd)

        # one market-data line feeds BOTH (mole's generic ticks superset)
        self.client.reqMarketDataType(1)
        self.client.reqMktData(MKT_REQ_ID, cd.contract,
                               trademole.GENERIC_TICKS, False, False, [])

        watch = trademole.parse_lifetime(MOLE_LIFETIME_STR)
        self._watch_timer = threading.Timer(watch, self._on_watch_timeout)
        self._watch_timer.daemon = True
        self._watch_timer.start()
        logger.info("[%s] duo armed on client %d (watch %ds, iti_baseline=%.2f, "
                    "trade_size_baseline=%.2f, lastClose=%s)", self.symbol,
                    self.client.client_id, watch, self.iti_baseline,
                    self.trade_size_baseline, self.last_close)
        return True

    # ------------------------------------------------------------------ #
    def on_buy_signal(self, last_ask):
        """Called by trade-mole on the ibapi thread when the surge fires.
        Cancels the watch window and spawns a sentinel thread that waits for
        the Sentiment gate before handing off to x-wing."""
        with self._lock:
            if self._buy_fired or self._finished:
                return
            self._buy_fired = True
            if self._watch_timer is not None:
                self._watch_timer.cancel()
        logger.info("[%s] BUY-SIGNAL (last_ask=%s) on client %d — awaiting sentiment gate",
                    self.symbol, last_ask, self.client.client_id)
        t = threading.Thread(target=self._await_sentiment_and_launch,
                             args=(last_ask,),
                             name=f"sentiment-gate-{self.symbol}", daemon=True)
        t.start()

    def _await_sentiment_and_launch(self, last_ask):
        """Sentinel thread: waits for the Sentiment gate then launches x-wing,
        or aborts the trade on timeout."""
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
        self.xwing.on_buy_signal(last_ask)
        t = threading.Thread(target=xwing.run_trade,
                             args=(self.xwing, time.time()),
                             name=f"xwing-{self.symbol}", daemon=True)
        t.start()

    def _on_watch_timeout(self):
        with self._lock:
            if self._buy_fired or self._finished:
                return
        logger.info("[%s] watch window elapsed with no surge; releasing client %d",
                    self.symbol, self.client.client_id)
        self.finish("no_surge")

    def _on_xwing_done(self, reason):
        # x-wing's run_trade has already flattened + finalized its TSV.
        self.finish(f"xwing:{reason}")

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
        logger.info("[%s] finishing duo on client %d (%s)",
                    self.symbol, self.client.client_id, reason)

        # Cancel a still-pending closed-market defer timer (e.g. shutdown or an
        # early release before 04:00) so it can't fire after teardown.
        if self._defer_timer is not None:
            self._defer_timer.cancel()

        try:
            self.client.cancelMktData(MKT_REQ_ID)
        except Exception as e:
            logger.debug("cancelMktData error: %s", e)

        # write the collected dataset (pre-trigger window + trigger row)
        try:
            trademole.write_records_output(self.mole, self.output_path,
                                           log=self.mole_log)
        except Exception as e:
            logger.error("[%s] writing trade-mole output failed: %s",
                         self.symbol, e, exc_info=True)

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
                 watchdog_interval=WATCHDOG_INTERVAL_S):
        self.host = host
        self.port = port
        self.client_qty = client_qty
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.watchdog_interval = watchdog_interval
        self.pool = ClientPool()
        self._stop = threading.Event()
        self._server_sock = None
        self._watchdog_thread = None
        self._sessions: dict[str, "DuoSession"] = {}   # ticker → active DuoSession
        self._sessions_lock = threading.Lock()

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

    def register_session(self, session: "DuoSession"):
        with self._sessions_lock:
            self._sessions[session.symbol] = session

    def unregister_session(self, symbol: str):
        with self._sessions_lock:
            self._sessions.pop(symbol, None)

    def release_client(self, client):
        self.pool.release(client)
        logger.info("client %d returned to pool (free=%d)",
                    client.client_id, self.pool.free_count)

    # ---- trigger handling ----
    def handle_trigger(self, payload: dict) -> dict:
        ticker = payload.get("ticker")
        if not ticker:
            return {"status": "rejected", "reason": "missing 'ticker'"}
        last_close = payload.get("lastDailyClose")
        iti = payload.get("itiBaseline")
        trade_size = payload.get("tradeSizeBaseline")

        # Pop free clients until we find a healthy one. Any disconnected client
        # is released back (the watchdog re-warms it); the `tried` guard stops
        # us spinning on the same dead clients within a single trigger.
        client = None
        tried = []
        while True:
            c = self.pool.acquire()
            if c is None:
                break
            if self._down(c):
                self.pool.release(c)
                if c in tried:        # cycled through all free clients; all dead
                    break
                tried.append(c)
                continue
            client = c
            break
        if client is None:
            logger.warning("[%s] no healthy free client; trigger rejected", ticker)
            return {"status": "rejected", "reason": "no_free_client"}

        session = DuoSession(self, client, ticker, last_close, iti, trade_size)
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
                with self._sessions_lock:
                    session = self._sessions.get(ticker)
                if session and payload["Sentiment"] == "OK":
                    session.set_sentiment_ok()
                    reply = {"status": "sentiment_ack", "symbol": ticker}
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
        description="clerk-1.0: warm ibapi client pool running trade-mole + "
                    "x-wing duos on shared connections.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--client-qty", type=int, required=True,
                   help=f"Number of warm clients to connect (ids start at "
                        f"{CLIENT_ID_START}; max id {CLIENT_ID_MAX}).")
    p.add_argument("--host", default="127.0.0.1", help="IBKR Gateway/TWS host")
    p.add_argument("--port", type=int, default=4001,
                   help="IBKR port (4001 live GW / 4002 paper GW / 7497 paper TWS)")
    p.add_argument("--listen-host", default="127.0.0.1",
                   help="Host/interface for the orch-trigger TCP socket")
    p.add_argument("--listen-port", type=int, default=8765,
                   help="TCP port for orch-triggers (JSON per line)")
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

    clerk = Clerk(args.host, args.port, args.client_qty,
                  args.listen_host, args.listen_port,
                  watchdog_interval=args.watchdog_interval)

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

    clerk.start_watchdog()

    try:
        clerk.serve()
    except Exception as e:
        logger.error("server error: %s", e, exc_info=True)
    finally:
        clerk.shutdown()


if __name__ == "__main__":
    main()
