#!/usr/bin/env python3
"""
clerk-1.0.py — orchestrator-facing client pool that runs trade-mole + x-wing
duos in-process on ONE shared ibapi connection each.

Why this exists
---------------
trade-mole-2.0 (surge detector) and x-wing-2.0 (trailing-stop trader) used to
open their own ibapi connections. The clerk keeps a pool of warm, pre-connected
clients (clientIds 1000..1000+N-1) and, on an orch-trigger, runs BOTH scripts on
ONE of those shared sockets:

    orchestrator ──TCP/JSON──► clerk
        {"ticker","lastDailyClose","itiBaseline"}
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
    python3 clerk-1.0.py --client-qty 10 --port 4001 --listen-port 8765

Trigger (from the orchestrator, one JSON object per line)::

    echo '{"ticker":"JWEL","lastDailyClose":2.35,"itiBaseline":44444.0}' \
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

# trade-mole driven-mode artifacts
MOLE_OUTPUT_DIR = "mole-outputs"
MOLE_LOG_DIR = "mole-logs"
MOLE_LIFETIME_STR = "01:00"     # watch window (mm:ss, per trade_mole convention)
DEFAULT_ITI_BASELINE = 44444.00  # used when the orch-trigger has no ITI baseline


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
trademole = _load_module("trademole2", "trade-mole-2.0.py")


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
                 last_close, iti_baseline):
        self.clerk = clerk
        self.client = client
        self.symbol = ticker.upper()
        self.last_close = last_close
        self.iti_baseline = iti_baseline if (iti_baseline and iti_baseline > 0) \
            else DEFAULT_ITI_BASELINE

        self._lock = threading.Lock()
        self._buy_fired = False
        self._finished = False
        self._watch_timer = None
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
        """Bind to the client, resolve the contract, subscribe to market data,
        and arm the watch timer. Returns True on success."""
        self.client.session = self

        # resolve contract (+ minTick) once on the shared client
        self.client.contract_evt.clear()
        self.client._contract_details = None
        self.client.reqContractDetails(CD_REQ_ID, xwing.make_contract(self.symbol))
        if not self.client.contract_evt.wait(timeout=15) or \
                self.client._contract_details is None:
            logger.error("[%s] contract resolution failed on client %d",
                         self.symbol, self.client.client_id)
            self.client.session = None
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
                    "lastClose=%s)", self.symbol, self.client.client_id, watch,
                    self.iti_baseline, self.last_close)
        return True

    # ------------------------------------------------------------------ #
    def on_buy_signal(self, last_ask):
        """Called by trade-mole on the client's run thread when the surge fires.
        Cancel the watch window, fire the x-wing entry, and hand control to a
        dedicated control-loop thread."""
        with self._lock:
            if self._buy_fired or self._finished:
                return
            self._buy_fired = True
            if self._watch_timer is not None:
                self._watch_timer.cancel()
        logger.info("[%s] BUY-SIGNAL (last_ask=%s) -> x-wing entry on client %d",
                    self.symbol, last_ask, self.client.client_id)
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
        logger.info("[%s] finishing duo on client %d (%s)",
                    self.symbol, self.client.client_id, reason)

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
    def __init__(self, host, port, client_qty, listen_host, listen_port):
        self.host = host
        self.port = port
        self.client_qty = client_qty
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.pool = ClientPool()
        self._stop = threading.Event()
        self._server_sock = None

    # ---- pool lifecycle ----
    def connect_pool(self):
        for i in range(self.client_qty):
            cid = CLIENT_ID_START + i
            c = SharedIBClient(cid, self.host, self.port)
            logger.info("connecting client %d to %s:%d ...", cid, self.host, self.port)
            c.connect(self.host, self.port, cid)
            threading.Thread(target=c.run, daemon=True,
                             name=f"ibapi-{cid}").start()
            if not c.connected_evt.wait(timeout=15):
                raise RuntimeError(f"client {cid} did not connect within 15s")
            self.pool.add(c)
        logger.info("pool ready: %d warm clients (ids %d..%d)", self.client_qty,
                    CLIENT_ID_START, CLIENT_ID_START + self.client_qty - 1)

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

        client = self.pool.acquire()
        if client is None:
            logger.warning("[%s] no free client; trigger rejected", ticker)
            return {"status": "rejected", "reason": "no_free_client"}
        if client.disconnected_flag:
            return {"status": "rejected", "reason": "client_disconnected"}

        session = DuoSession(self, client, ticker, last_close, iti)
        try:
            ok = session.start()
        except Exception as e:
            logger.error("[%s] session start failed: %s", ticker, e, exc_info=True)
            ok = False
        if not ok:
            self.release_client(client)
            return {"status": "rejected", "reason": "session_start_failed"}
        return {"status": "accepted", "clientId": client.client_id,
                "symbol": session.symbol}

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
            reply = self.handle_trigger(payload)
            try:
                conn.sendall((json.dumps(reply) + "\n").encode("utf-8"))
            except Exception:
                pass

    def shutdown(self):
        self._stop.set()
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
    os.makedirs("logs", exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    fh = logging.FileHandler(os.path.join("logs", f"clerk_{ts}.log"))
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
                  args.listen_host, args.listen_port)

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

    try:
        clerk.serve()
    except Exception as e:
        logger.error("server error: %s", e, exc_info=True)
    finally:
        clerk.shutdown()


if __name__ == "__main__":
    main()
