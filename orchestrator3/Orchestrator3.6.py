import sys
import os
import csv
import json
import uuid
import time
import signal
import logging
import threading
import subprocess
import importlib.util as _ilu
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, wait as _futures_wait
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import glob

sys.path.insert(0, '/home/tom/Documents/ibkr_scripts/N2/scripts/newswatcher3')
sys.path.insert(0, '/home/tom/Documents/ibkr_scripts/N2/scripts/universe_finder')
sys.path.insert(0, '/home/tom/Documents/ibkr_scripts/N2/scripts/FinBERT_pipeline/FinBERT_body_noCoref')

# ── FinBERT import (hyphen in filename prevents normal import) ────────────────
_finbert_spec = _ilu.spec_from_file_location(
    "FinBERT_headliner",
    "/home/tom/Documents/ibkr_scripts/N2/scripts/FinBERT/FinBERT-headliner.py",
)
_finbert_mod = _ilu.module_from_spec(_finbert_spec)
_finbert_spec.loader.exec_module(_finbert_mod)
analyze_headline = _finbert_mod.analyze_headline
load_model       = _finbert_mod.load_model
# ─────────────────────────────────────────────────────────────────────────────

# ── FinBERT body pipeline (noCoref) + neutral-management add-on ───────────────
from FinBERT_body_noCoref import FinBERTBodyPipeline
from finBERT_neutral_management_addON import aggregate as _nocoref_aggregate
# ─────────────────────────────────────────────────────────────────────────────

# ── Daily TSV output ──────────────────────────────────────────────────────────
OUTPUT_DIR = "/home/tom/Documents/ibkr_scripts/N2/scripts/orchestrator3/tables"
# ─────────────────────────────────────────────────────────────────────────────

import NewsWatcher4 as nw

# ── NewsWatcher4 inputs (RTPR alerts WS + permalink curl) ─────────────────────
#
# PREREQUISITE: a filter rule must already exist on https://rtpr.io/wire.
#   Recommended catch-all:  tickers_length gte 1
# Without it, the alerts WS connects but emits no `alert` messages.
#
NW4_UNIVERSE_TSV          = '/home/tom/Documents/ibkr_scripts/N2/scripts/universe_finder/data/nasdaq_symbols_data_priced.tsv'
NW4_PRICED_TSV            = '/home/tom/Documents/ibkr_scripts/N2/scripts/universe_finder/data/nasdaq_symbols_data_priced.tsv'
NW4_BLACK_LIST            = '/home/tom/Documents/ibkr_scripts/N2/scripts/orchestrator3/black_list.csv'
NW4_API_KEYS              = '/home/tom/Documents/ibkr_scripts/N2/scripts/newswatcher3/RTPR_API-Key.txt'
NW4_LOG_DIR               = '/home/tom/Documents/ibkr_scripts/N2/scripts/orchestrator3/logs'
NW4_OUTPUT_DIR            = '/home/tom/Documents/ibkr_scripts/N2/scripts/orchestrator3/outputs'
NW4_NEWS_DF_DIR           = '/home/tom/Documents/ibkr_scripts/N2/scripts/orchestrator3/outputs'
NW4_BLOCKED_DIR           = '/home/tom/Documents/ibkr_scripts/N2/scripts/orchestrator3/outputs/blocked_PRs'
NW4_ACCEPTED_DIR          = '/home/tom/Documents/ibkr_scripts/N2/scripts/orchestrator3/outputs/accepted_PRs'
NW4_EXCLUDED_STRINGS_FILE = '/home/tom/Documents/ibkr_scripts/N2/scripts/newswatcher3/excluded_strings.txt'
TM_EXCLUDED_STRINGS_FILE  = '/home/tom/Documents/ibkr_scripts/N2/scripts/orchestrator3/excluded_strings-2.txt'
NW4_BLACKLIST_EXPIRY_HOURS = 0
NW4_REJECT_FLOAT_GT       = 50        # M shares; matches old universe filter
NW4_REJECT_PRICE_GT       = 10.00
NW4_FLUSH_INTERVAL_SEC    = 3600
# ─────────────────────────────────────────────────────────────────────────────

# ── Trade-mole trigger (shared-client pool, v4.2) ─────────────────────────────
TM_SENTIMENT_SCORE_MIN = 0.7     # launch only if sentiment_score > this
TM_FLOAT_MAX_M         = 50      # launch only if Float (millions) < this

TM_SCRIPT          = '/home/tom/Documents/ibkr_scripts/N2/scripts/volume/trade_surge_mole/trade_mole_4.2.py'
TM_CLIENT_IDS      = [100, 101, 102]   # 3 long-lived IBKR client connections
TM_SLOTS_PER_CLIENT = 10               # capacity: 30 symbols total
TM_MAX_EXTENSIONS  = 3                 # max times a symbol's lifeTime can be extended
TM_LIFETIME_SEC    = 600               # 10 minutes per line (replaces v3.5 '10:00')
TM_OUTPUT_DIR      = '/home/tom/Documents/ibkr_scripts/N2/scripts/orchestrator3/trade_mole_outputs'
TM_LOG_DIR         = '/home/tom/Documents/ibkr_scripts/N2/scripts/orchestrator3/trade_mole_logs'
TM_HOST            = '127.0.0.1'
TM_PORT            = 4001        # 4001=live GW  4002=paper GW  7496/7497=TWS
TM_PYTHON          = sys.executable
TM_DEFAULT_BASELINE_ITI = 44444.0
TM_ITI_DATA_DIR    = '/home/tom/Documents/ibkr_scripts/N2/scripts/universe_finder/data'

TM_MANAGER_READY_TIMEOUT_SEC = 30        # per-manager wait at Orchestrator startup
TM_LAUNCH_ACK_TIMEOUT_SEC    = 5         # per-launch IPC ack
TM_RESTART_BACKOFFS_SEC      = [5, 15, 60, 300]   # then steady at 300
TM_RESTART_ATTEMPT_RESET_SEC = 300       # if manager runs > this, attempt counter resets
TM_SHUTDOWN_GRACE_SEC        = 30        # how long to wait for graceful manager exit
# ─────────────────────────────────────────────────────────────────────────────

# ── FinBERT body pipeline (noCoref) + neutral-management ──────────────────────
BODY_FINBERT_WORKERS      = 2     # one pipeline instance per worker thread
BODY_FINBERT_TIMEOUT_SEC  = 180   # per-article ceiling; trade_mole already fired by now
NOCOREF_NEUTRAL_THRESHOLD = 0.85  # method 1 (neutral_filter)
NOCOREF_TOP_K             = 3     # method 4 (top_k)
NOCOREF_POSITIONAL_DECAY  = 0.1   # method 5 (positional)
NOCOREF_SENTENCES_TO_ANALYSE = 20  # cap spaCy sentences scored per article (None = all)
# ─────────────────────────────────────────────────────────────────────────────

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger('Orchestrator3.6')

_iti_df: pd.DataFrame = None
_last_iti_reload_date = None
_iti_df_lock = threading.Lock()

_excluded_strings: set[str] = set()


def _load_excluded_strings(path: str) -> None:
    global _excluded_strings
    try:
        with open(path, encoding='utf-8') as f:
            strings = {line.strip() for line in f if line.strip()}
        _excluded_strings = strings
        logger.info(f"[Filter] Loaded {len(strings)} excluded strings from {path}: {sorted(strings)}")
    except Exception as exc:
        logger.warning(f"[Filter] Could not load {path}: {exc} — no headline filtering")

# ─── ThreadPoolExecutors ──────────────────────────────────────────────────────

_finbert_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix='finbert')
_collect_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix='collect')

# Dedicated tiny executor so `maybe_launch_trade_mole` can submit pool.launch
# without blocking the collect worker for up to TM_LAUNCH_ACK_TIMEOUT_SEC.
# This preserves the fire-and-forget semantics of v3.5's subprocess.Popen path
# even though pool.launch is now a synchronous-ack RPC.
_tm_launch_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix='tm-launch')

# Body pipeline is NOT thread-safe (shared spaCy nlp, ONNX session,
# TickerResolver cache). Each worker gets its own instance loaded once via the
# ThreadPoolExecutor initializer.
_body_local = threading.local()


def _init_body_worker() -> None:
    logger.info(
        f"[Body] thread {threading.current_thread().name}: "
        f"loading FinBERTBodyPipeline (noCoref)..."
    )
    pipe = FinBERTBodyPipeline(
        write_outputs=False,
        sentences_to_analyse=NOCOREF_SENTENCES_TO_ANALYSE,
    )
    pipe.load_models()
    _body_local.pipeline = pipe
    logger.info(f"[Body] thread {threading.current_thread().name}: pipeline ready.")


_body_executor = ThreadPoolExecutor(
    max_workers=BODY_FINBERT_WORKERS,
    thread_name_prefix='body',
    initializer=_init_body_worker,
)

_tsv_lock = threading.Lock()


# ============================================================================
# TradeMolePool — owns the 3 long-lived trade_mole_4.2 manager subprocesses
# ============================================================================

class TradeMolePool:
    """
    Drives 3 long-lived trade_mole_4.2.py manager subprocesses (clientIDs
    100/101/102 by default). Each manager serves up to ``slots_per_client``
    in-process per-symbol "lines". This pool:

      * spawns the managers at startup and waits for each to emit
        ``{"event":"ready"}`` on stdout (strict: aborts on timeout);
      * provides ``launch(symbol, baseline_iti)`` for the news pipeline —
        round-robins across managers with spillover and, for an
        already-active symbol, sends EXTEND instead of LAUNCH;
      * maintains a global ``symbol -> (client_id, slot, end_time, extensions)``
        table so the same ticker never runs on two managers at once;
      * monitors manager PIDs and auto-restarts with exponential backoff;
      * on shutdown, asks each manager to drain gracefully, then SIGTERM /
        SIGKILL if needed.

    Thread model
    ------------
    * Reader threads (one per manager): parse stdout JSON acks, update state.
    * Monitor thread: every 1s, poll() each manager. On exit, scrub state
      and spawn a respawn thread.
    * Respawn threads: short-lived; wait backoff, restart manager, wait READY.
    * Caller threads (collect workers / tm-launch executor): call ``launch()``
      / ``shutdown()``.

    Locking
    -------
    ``_state_lock`` guards ``_procs``, ``_symbol_owner``, ``_client_occupancy``,
    ``_rr_pointer``, ``_pending_acks``, ``_restart_attempts``, ``_last_alive_at``.
    Per-manager stdin writes are serialized by ``_stdin_locks[cid]``.
    Critical: ``launch()`` releases ``_state_lock`` BEFORE waiting on an ack
    event, so the reader thread can take the lock to signal it (no deadlock).
    """

    def __init__(
        self,
        client_ids: list,
        slots_per_client: int,
        max_extensions: int,
        lifetime_sec: int,
        python_exec: str,
        manager_script: str,
        host: str,
        port: int,
        output_dir: str,
        log_dir: str,
        ready_timeout_sec: int = TM_MANAGER_READY_TIMEOUT_SEC,
        ack_timeout_sec: int = TM_LAUNCH_ACK_TIMEOUT_SEC,
    ):
        self.client_ids = list(client_ids)
        self.slots_per_client = slots_per_client
        self.max_extensions = max_extensions
        self.lifetime_sec = lifetime_sec
        self.python_exec = python_exec
        self.manager_script = manager_script
        self.host = host
        self.port = port
        self.output_dir = output_dir
        self.log_dir = log_dir
        self.ready_timeout_sec = ready_timeout_sec
        self.ack_timeout_sec = ack_timeout_sec

        self._state_lock = threading.RLock()
        self._stop_event = threading.Event()

        # Per-manager state
        self._procs: dict = {}                # cid -> subprocess.Popen
        self._stdin_locks: dict = {}          # cid -> threading.Lock
        self._ready_events: dict = {}         # cid -> threading.Event
        self._reader_threads: dict = {}       # cid -> threading.Thread
        self._client_occupancy: dict = {}     # cid -> int
        self._restart_attempts: dict = {}     # cid -> int
        self._last_alive_at: dict = {}        # cid -> wall-clock when last spawned

        # Global symbol table (dedup + EXTEND routing)
        # symbol -> {"client_id": int, "slot": int, "end_time_epoch": float,
        #            "extensions": int}
        self._symbol_owner: dict = {}

        # Round-robin pointer (index into self.client_ids)
        self._rr_pointer: int = 0

        # Pending IPC acks
        # req_id -> {"event": threading.Event, "result": Optional[dict]}
        self._pending_acks: dict = {}

        self._monitor_thread: threading.Thread = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self):
        """Spawn all managers, start reader/monitor threads, block for READY."""
        logger.info(
            f"[TM] starting pool: clients={self.client_ids} "
            f"slots/client={self.slots_per_client} "
            f"lifeTime={self.lifetime_sec}s max_ext={self.max_extensions}"
        )
        for cid in self.client_ids:
            self._stdin_locks[cid] = threading.Lock()
            self._ready_events[cid] = threading.Event()
            self._client_occupancy[cid] = 0
            self._restart_attempts[cid] = 0

        for cid in self.client_ids:
            self._spawn_manager(cid)

        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name='tm-monitor',
        )
        self._monitor_thread.start()

        # Strict: every manager must become READY within the timeout
        for cid in self.client_ids:
            if not self._ready_events[cid].wait(timeout=self.ready_timeout_sec):
                # Abort — caller (main()) decides whether to exit
                raise RuntimeError(
                    f"trade_mole manager {cid} failed to become READY within "
                    f"{self.ready_timeout_sec}s"
                )
            logger.info(f"[TM] manager {cid} READY")
        logger.info("[TM] all managers READY — pool active")

    def shutdown(self):
        """Ask all managers to flush + exit, escalating to SIGTERM/SIGKILL."""
        if self._stop_event.is_set():
            return
        self._stop_event.set()
        logger.info("[TM] shutting down trade_mole pool...")

        with self._state_lock:
            procs = dict(self._procs)

        # 1) Polite shutdown via IPC
        for cid in list(procs.keys()):
            try:
                self._send(cid, {"cmd": "shutdown"})
            except Exception as e:
                logger.debug(f"[TM] shutdown send to {cid} failed: {e}")

        # 2) Close stdin so the manager's stdin reader exits (also cooperative)
        for cid, proc in procs.items():
            try:
                if proc.stdin and not proc.stdin.closed:
                    proc.stdin.close()
            except Exception:
                pass

        # 3) Wait for graceful exit
        deadline = time.time() + TM_SHUTDOWN_GRACE_SEC
        for cid, proc in procs.items():
            remaining = max(0.5, deadline - time.time())
            try:
                proc.wait(timeout=remaining)
                logger.info(f"[TM] manager {cid} exited cleanly")
            except subprocess.TimeoutExpired:
                logger.warning(f"[TM] manager {cid} did not exit in {TM_SHUTDOWN_GRACE_SEC}s — SIGTERM")
                try:
                    proc.terminate()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    logger.warning(f"[TM] manager {cid} still alive — SIGKILL")
                    try:
                        proc.kill()
                    except Exception:
                        pass

        if self._monitor_thread is not None:
            self._monitor_thread.join(timeout=2.0)
        logger.info("[TM] pool shutdown complete")

    # ------------------------------------------------------------------
    # Public launch API — called by maybe_launch_trade_mole
    # ------------------------------------------------------------------

    def launch(self, symbol: str, baseline_iti: float) -> str:
        """
        Drive a new line for ``symbol`` (or extend an existing one).

        Returns a short status string for logging:
        ``"extended"``, ``"launched:<client_id>"``, ``"dropped:max_extensions"``,
        ``"dropped:all_full"``, ``"dropped:no_managers"``.
        """
        if not self.client_ids:
            return "dropped:no_managers"

        # Phase 1: EXTEND path — if symbol is already active, bump its deadline
        ext_status = self._try_extend(symbol)
        if ext_status is not None:
            return ext_status

        # Phase 2: RR LAUNCH path — try each manager in turn until one accepts
        return self._try_launch(symbol, baseline_iti)

    # ------------------------------------------------------------------
    # EXTEND path
    # ------------------------------------------------------------------

    def _try_extend(self, symbol: str):
        """Return a status string if an EXTEND attempt was made, else None."""
        with self._state_lock:
            owner = self._symbol_owner.get(symbol)
            if owner is None:
                return None
            client_id = owner["client_id"]
            if owner["extensions"] >= self.max_extensions:
                logger.info(
                    f"[TM] {symbol}: already at max extensions "
                    f"({owner['extensions']}/{self.max_extensions}) — drop"
                )
                return "dropped:max_extensions"
            req_id = uuid.uuid4().hex
            ack_event = self._register_pending(req_id)

        # Send + wait OUTSIDE the lock so the reader thread can signal back
        try:
            self._send(client_id, {
                "cmd": "extend",
                "req_id": req_id,
                "symbol": symbol,
                "additional_sec": self.lifetime_sec,
            })
        except Exception as e:
            logger.warning(f"[TM] {symbol}: extend send to {client_id} failed: {e}")
            self._consume_pending(req_id)
            return "dropped:send_failed"

        ack_event.wait(timeout=self.ack_timeout_sec)
        result = self._consume_pending(req_id)
        if result is None:
            logger.warning(f"[TM] {symbol}: extend ack timeout on client {client_id}")
            return "dropped:extend_timeout"

        event = result.get("event")
        if event == "extended":
            logger.info(
                f"[TM] {symbol}: extended on client {client_id} "
                f"(extensions={result.get('extensions')})"
            )
            return "extended"
        if event == "extend_failed":
            reason = result.get("reason")
            if reason == "max_extensions":
                logger.info(
                    f"[TM] {symbol}: manager-side max extensions reached — drop"
                )
                return "dropped:max_extensions"
            if reason == "not_active":
                # Race: manager already freed this symbol; clean stale entry
                # and fall through to a fresh LAUNCH.
                logger.info(
                    f"[TM] {symbol}: extend race (already freed) — relaunching"
                )
                with self._state_lock:
                    self._symbol_owner.pop(symbol, None)
                return None
            logger.warning(f"[TM] {symbol}: extend failed: {result}")
            return f"dropped:extend_failed:{reason}"
        logger.warning(f"[TM] {symbol}: unexpected extend ack: {result}")
        return "dropped:extend_unexpected"

    # ------------------------------------------------------------------
    # RR LAUNCH path
    # ------------------------------------------------------------------

    def _try_launch(self, symbol: str, baseline_iti: float) -> str:
        """Round-robin over managers; first to accept wins. Advance RR pointer
        past the accepting client so the next launch starts at its successor."""
        n = len(self.client_ids)
        tried = set()
        accepted_client = None
        for i in range(n):
            with self._state_lock:
                idx = (self._rr_pointer + i) % n
                target = self.client_ids[idx]
                if target in tried:
                    continue
                # Skip dead managers (no proc registered)
                if target not in self._procs:
                    tried.add(target)
                    continue
                # Skip full clients
                if self._client_occupancy.get(target, 0) >= self.slots_per_client:
                    tried.add(target)
                    continue
                req_id = uuid.uuid4().hex
                ack_event = self._register_pending(req_id)

            try:
                self._send(target, {
                    "cmd": "launch",
                    "req_id": req_id,
                    "symbol": symbol,
                    "baseline_iti": baseline_iti,
                    "lifeTime_sec": self.lifetime_sec,
                    "output_dir": self.output_dir,
                    "log_dir": self.log_dir,
                })
            except Exception as e:
                logger.warning(
                    f"[TM] {symbol}: launch send to client {target} failed: {e}"
                )
                self._consume_pending(req_id)
                tried.add(target)
                continue

            ack_event.wait(timeout=self.ack_timeout_sec)
            result = self._consume_pending(req_id)
            if result is None:
                logger.warning(
                    f"[TM] {symbol}: launch ack timeout on client {target}"
                )
                tried.add(target)
                continue
            event = result.get("event")
            if event == "accepted":
                accepted_client = target
                with self._state_lock:
                    self._rr_pointer = (self.client_ids.index(target) + 1) % n
                logger.info(
                    f"[TM] {symbol}: launched on client {target} "
                    f"slot={result.get('slot')}"
                )
                break
            if event == "full":
                tried.add(target)
                continue
            logger.warning(
                f"[TM] {symbol}: unexpected launch ack on {target}: {result}"
            )
            tried.add(target)
            continue

        if accepted_client is None:
            with self._state_lock:
                occ = dict(self._client_occupancy)
            logger.warning(
                f"[TM] {symbol}: no slot accepted — dropping. occupancy={occ}"
            )
            return "dropped:all_full"
        return f"launched:{accepted_client}"

    # ------------------------------------------------------------------
    # Pending-ack registry helpers (called under or outside lock)
    # ------------------------------------------------------------------

    def _register_pending(self, req_id: str) -> threading.Event:
        """Caller must hold _state_lock."""
        ev = threading.Event()
        self._pending_acks[req_id] = {"event": ev, "result": None}
        return ev

    def _consume_pending(self, req_id: str):
        """Pop the entry and return its result (or None if never signalled)."""
        with self._state_lock:
            entry = self._pending_acks.pop(req_id, None)
        if entry is None:
            return None
        if entry["event"].is_set():
            return entry["result"]
        return None

    def _signal_ack(self, req_id, msg):
        """Caller must hold _state_lock."""
        if req_id is None:
            return
        entry = self._pending_acks.get(req_id)
        if entry is not None:
            entry["result"] = msg
            entry["event"].set()

    # ------------------------------------------------------------------
    # Manager subprocess plumbing
    # ------------------------------------------------------------------

    def _build_manager_argv(self, client_id: int) -> list:
        return [
            self.python_exec, self.manager_script,
            "--client-id", str(client_id),
            "--host", self.host,
            "--port", str(self.port),
            "--max-slots", str(self.slots_per_client),
            "--max-extensions", str(self.max_extensions),
            "--manager-log-dir", self.log_dir,
        ]

    def _spawn_manager(self, client_id: int):
        argv = self._build_manager_argv(client_id)
        logger.info(f"[TM] spawning manager {client_id}: {' '.join(argv)}")
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,    # detach so Orchestrator Ctrl+C is not forwarded
            close_fds=True,
            text=True,
            bufsize=1,                 # line-buffered
        )
        with self._state_lock:
            self._procs[client_id] = proc
            self._client_occupancy[client_id] = 0  # fresh start
            self._ready_events[client_id].clear()
            self._last_alive_at[client_id] = time.time()
        # Reader thread for this manager's stdout
        t = threading.Thread(
            target=self._stdout_reader_loop, args=(client_id, proc),
            daemon=True, name=f"tm-reader-{client_id}",
        )
        with self._state_lock:
            self._reader_threads[client_id] = t
        t.start()

    def _send(self, client_id: int, payload: dict):
        """Write one JSON line to the manager's stdin (thread-safe)."""
        with self._state_lock:
            proc = self._procs.get(client_id)
            lock = self._stdin_locks.get(client_id)
        if proc is None or proc.stdin is None or proc.stdin.closed:
            raise RuntimeError(f"manager {client_id} stdin not available")
        line = json.dumps(payload, separators=(",", ":")) + "\n"
        with lock:
            proc.stdin.write(line)
            proc.stdin.flush()

    # ------------------------------------------------------------------
    # Stdout reader — one per manager
    # ------------------------------------------------------------------

    def _stdout_reader_loop(self, client_id: int, proc: subprocess.Popen):
        try:
            for raw in proc.stdout:
                line = raw.strip()
                if not line:
                    continue
                # Manager spec: stdout is reserved for JSON acks. Anything else
                # is unexpected (a print() somewhere). Log it and skip.
                if not line.startswith('{'):
                    logger.debug(f"[TM mgr {client_id}] non-JSON: {line!r}")
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError as e:
                    logger.warning(
                        f"[TM mgr {client_id}] bad JSON: {e} | {line!r}"
                    )
                    continue
                try:
                    self._dispatch_event(client_id, msg)
                except Exception:
                    logger.exception(
                        f"[TM mgr {client_id}] event dispatch failed: {msg!r}"
                    )
        except Exception as e:
            logger.warning(f"[TM mgr {client_id}] reader loop crashed: {e}")
        logger.info(f"[TM mgr {client_id}] stdout closed (reader thread exiting)")

    def _dispatch_event(self, client_id: int, msg: dict):
        event = msg.get("event")
        if event == "ready":
            self._ready_events[client_id].set()
            logger.info(f"[TM] manager {client_id} reports READY")
            return

        if event == "accepted":
            req_id = msg.get("req_id")
            symbol = msg.get("symbol")
            slot = msg.get("slot")
            end_time = msg.get("end_time_epoch")
            with self._state_lock:
                self._symbol_owner[symbol] = {
                    "client_id": client_id,
                    "slot": slot,
                    "end_time_epoch": end_time,
                    "extensions": 0,
                }
                self._client_occupancy[client_id] = \
                    self._client_occupancy.get(client_id, 0) + 1
                self._signal_ack(req_id, msg)
            return

        if event == "full":
            req_id = msg.get("req_id")
            with self._state_lock:
                self._signal_ack(req_id, msg)
            return

        if event == "extended":
            req_id = msg.get("req_id")
            symbol = msg.get("symbol")
            with self._state_lock:
                owner = self._symbol_owner.get(symbol)
                if owner is not None:
                    if msg.get("new_end_time_epoch") is not None:
                        owner["end_time_epoch"] = msg["new_end_time_epoch"]
                    if msg.get("extensions") is not None:
                        owner["extensions"] = msg["extensions"]
                self._signal_ack(req_id, msg)
            return

        if event == "extend_failed":
            req_id = msg.get("req_id")
            symbol = msg.get("symbol")
            with self._state_lock:
                if msg.get("reason") == "max_extensions":
                    owner = self._symbol_owner.get(symbol)
                    if owner is not None and msg.get("extensions") is not None:
                        owner["extensions"] = msg["extensions"]
                self._signal_ack(req_id, msg)
            return

        if event == "freed":
            symbol = msg.get("symbol")
            slot = msg.get("slot")
            with self._state_lock:
                if symbol in self._symbol_owner:
                    # Only remove if this manager actually owns it (defensive
                    # against late acks from a manager that was respawned)
                    if self._symbol_owner[symbol]["client_id"] == client_id:
                        del self._symbol_owner[symbol]
                self._client_occupancy[client_id] = max(
                    0, self._client_occupancy.get(client_id, 0) - 1
                )
            logger.info(f"[TM] manager {client_id} freed {symbol} (slot {slot})")
            return

        logger.warning(f"[TM mgr {client_id}] unknown event: {msg!r}")

    # ------------------------------------------------------------------
    # Monitor + respawn
    # ------------------------------------------------------------------

    def _monitor_loop(self):
        """Watch manager PIDs every 1s; on exit, scrub state and respawn."""
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=1.0)
            if self._stop_event.is_set():
                break
            with self._state_lock:
                procs = dict(self._procs)
            for cid, proc in procs.items():
                rc = proc.poll()
                if rc is None:
                    # Still alive; reset attempt counter if up long enough
                    with self._state_lock:
                        spawned_at = self._last_alive_at.get(cid, 0)
                    if (time.time() - spawned_at) > TM_RESTART_ATTEMPT_RESET_SEC:
                        with self._state_lock:
                            if self._restart_attempts.get(cid, 0) > 0:
                                logger.info(
                                    f"[TM] manager {cid} stable for "
                                    f"{TM_RESTART_ATTEMPT_RESET_SEC}s — "
                                    f"resetting backoff"
                                )
                                self._restart_attempts[cid] = 0
                    continue
                # Manager exited
                logger.error(f"[TM] manager {cid} exited rc={rc}")
                self._handle_manager_exit(cid)

    def _handle_manager_exit(self, client_id: int):
        """Scrub state for a dead manager and schedule a respawn."""
        with self._state_lock:
            # Remove all symbols this manager owned
            to_remove = [
                s for s, o in self._symbol_owner.items()
                if o.get("client_id") == client_id
            ]
            for s in to_remove:
                self._symbol_owner.pop(s, None)
            if to_remove:
                logger.warning(
                    f"[TM] manager {client_id} crash: lost in-flight symbols "
                    f"{to_remove}"
                )
            self._client_occupancy[client_id] = 0
            self._procs.pop(client_id, None)
            self._reader_threads.pop(client_id, None)
            self._ready_events[client_id].clear()
            # Cancel any pending acks targeting this client
            stale = [rid for rid, e in self._pending_acks.items() if not e["event"].is_set()]
            for rid in stale:
                # Mark them aborted — they'll time out naturally; we don't
                # know which client each one targeted so we don't force-fail.
                pass

        if self._stop_event.is_set():
            return

        t = threading.Thread(
            target=self._respawn, args=(client_id,),
            daemon=True, name=f"tm-respawn-{client_id}",
        )
        t.start()

    def _respawn(self, client_id: int):
        if self._stop_event.is_set():
            return
        with self._state_lock:
            attempts = self._restart_attempts.get(client_id, 0)
        idx = min(attempts, len(TM_RESTART_BACKOFFS_SEC) - 1)
        backoff = TM_RESTART_BACKOFFS_SEC[idx]
        logger.warning(
            f"[TM] respawning manager {client_id} after {backoff}s "
            f"backoff (attempt {attempts + 1})"
        )
        self._stop_event.wait(timeout=backoff)
        if self._stop_event.is_set():
            return
        with self._state_lock:
            self._restart_attempts[client_id] = attempts + 1
        try:
            self._spawn_manager(client_id)
        except Exception as e:
            logger.exception(f"[TM] respawn failed for {client_id}: {e}")
            t = threading.Thread(
                target=self._respawn, args=(client_id,),
                daemon=True, name=f"tm-respawn-{client_id}",
            )
            t.start()
            return
        if not self._ready_events[client_id].wait(timeout=self.ready_timeout_sec):
            logger.error(
                f"[TM] respawned manager {client_id} failed READY within "
                f"{self.ready_timeout_sec}s — killing and retrying"
            )
            with self._state_lock:
                proc = self._procs.get(client_id)
            if proc is not None:
                try:
                    proc.terminate()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    try:
                        proc.kill()
                    except Exception:
                        pass
            self._handle_manager_exit(client_id)
            return
        logger.info(f"[TM] manager {client_id} respawned and READY")


# ─── Module-level pool handle (constructed in main()) ───────────────────────
_tm_pool: TradeMolePool = None


# ─── Analysis functions ───────────────────────────────────────────────────────

def analyze_finbert(news_dict: dict) -> dict:
    """FinBERT headline sentiment analysis. Runs once per article."""
    result = analyze_headline(news_dict['Headline'])
    logger.info(
        f"[FinBERT] {news_dict['Symbol']} → {result['label'].upper()} "
        f"(score={result['sentiment_score']:.4f})"
    )
    return {
        'positive':        result['positive'],
        'negative':        result['negative'],
        'neutral':         result['neutral'],
        'sentiment_score': result['sentiment_score'],
        'label':           result['label'],
    }


def _run_body_pipeline(news_dict: dict) -> dict:
    news_id = news_dict.get('ID')
    symbol  = news_dict.get('Symbol')

    body = (news_dict.get('article_body') or '').strip()
    if not body:
        logger.info(f"[Body] id={news_id} sym={symbol}: empty article_body — skipping")
        return {}

    tickers = [t.strip() for t in (symbol or '').split(',') if t.strip()]
    pipeline_input = dict(news_dict)
    pipeline_input['tickers'] = tickers

    pipe = _body_local.pipeline
    t0 = datetime.now()
    logger.info(f"[Body] id={news_id} sym={symbol}: starting pipeline")
    try:
        result = pipe.process(pipeline_input, write_outputs=False)
    except ValueError as exc:
        logger.warning(f"[Body] id={news_id}: {exc}")
        return {}
    except Exception as exc:
        logger.error(
            f"[Body] id={news_id} sym={symbol}: pipeline error: {exc}",
            exc_info=True,
        )
        return {}

    elapsed_ms = (datetime.now() - t0).total_seconds() * 1000.0
    found = result.get('finbert', {}).get('metadata', {}).get('unique_tickers', [])
    logger.info(
        f"[Body] id={news_id} sym={symbol}: done in {elapsed_ms:.0f}ms — "
        f"{len(found)} ticker(s) scored: {found}"
    )
    return result


def _compute_nocoref_scores(body_result: dict, symbol: str) -> dict:
    none_result = {
        'nocoref_neutral_filter':      None,
        'nocoref_confidence_weighted': None,
        'nocoref_net_score':           None,
        'nocoref_top_k':               None,
        'nocoref_positional':          None,
    }

    if not body_result:
        return none_result

    ticker_block = (
        body_result
        .get('finbert', {})
        .get('ticker_sentiments', {})
        .get(symbol)
    )
    if not ticker_block:
        return none_result

    sentences = ticker_block.get('sentences', [])
    if not sentences:
        return none_result

    def _agg(method: int) -> float:
        score, _used = _nocoref_aggregate(
            method, sentences,
            neutral_threshold=NOCOREF_NEUTRAL_THRESHOLD,
            top_k=NOCOREF_TOP_K,
            positional_decay=NOCOREF_POSITIONAL_DECAY,
        )
        return round(score, 4)

    return {
        'nocoref_neutral_filter':      _agg(1),
        'nocoref_confidence_weighted': _agg(2),
        'nocoref_net_score':           _agg(3),
        'nocoref_top_k':               _agg(4),
        'nocoref_positional':          _agg(5),
    }


# ─── Trigger evaluator ───────────────────────────────────────────────────────

def evaluate_trigger(completed_dict: dict) -> str:
    sentiment = completed_dict.get('sentiment_score')
    float_m   = completed_dict.get('Float')

    failures = []
    if sentiment is None or sentiment <= TM_SENTIMENT_SCORE_MIN:
        failures.append('sentiment_score')
    if float_m is None or float_m >= TM_FLOAT_MAX_M:
        failures.append('Float')
    headline = completed_dict.get('Headline') or ''
    if any(s.lower() in headline.lower() for s in _excluded_strings):
        failures.append('excluded_string')

    return 'YES' if not failures else 'NO:' + ','.join(failures)


# ─── Trade-mole launcher (delegates to TradeMolePool) ─────────────────────────

def _seconds_until_market_open() -> float:
    now_et = datetime.now(tz=ZoneInfo('America/New_York'))
    target = now_et.replace(hour=4, minute=0, second=0, microsecond=0)
    if now_et >= target:
        target += timedelta(days=1)
    return (target - now_et).total_seconds()


def _do_launch_trade_mole(completed_dict: dict) -> None:
    """Submit the actual pool.launch on the tm-launch executor so the calling
    collect worker (or threading.Timer thread) doesn't block waiting for the
    IPC ack. This preserves v3.5's fire-and-forget mental model."""
    symbol    = completed_dict['Symbol']
    sentiment = completed_dict.get('sentiment_score')
    float_m   = completed_dict.get('Float')
    baseline_iti = _lookup_baseline_iti(symbol)

    def _runner():
        try:
            status = _tm_pool.launch(symbol, baseline_iti)
            logger.info(
                f"[TM] {symbol}: pool.launch status={status} "
                f"sentiment={sentiment:.4f} float={float_m} "
                f"baseline_iti={baseline_iti:.2f}s"
            )
        except Exception as exc:
            logger.error(
                f"[TM] {symbol}: pool.launch raised: {exc}", exc_info=True,
            )

    try:
        _tm_launch_executor.submit(_runner)
    except Exception as exc:
        logger.error(f"[TM] {symbol}: failed to submit launch: {exc}", exc_info=True)


def maybe_launch_trade_mole(completed_dict: dict) -> None:
    """Fire-and-forget launch through the shared-client pool if trigger
    conditions are met for this single ticker."""
    symbol    = completed_dict['Symbol']
    sentiment = completed_dict.get('sentiment_score')
    float_m   = completed_dict.get('Float')

    if sentiment is None or sentiment <= TM_SENTIMENT_SCORE_MIN:
        return
    if float_m is None or float_m >= TM_FLOAT_MAX_M:
        return
    headline = completed_dict.get('Headline') or ''
    if any(s.lower() in headline.lower() for s in _excluded_strings):
        return

    _et_hour = datetime.now(tz=ZoneInfo('America/New_York')).hour
    if _et_hour >= 20 or _et_hour < 4:
        delay = _seconds_until_market_open()
        t = threading.Timer(delay, _do_launch_trade_mole, args=[completed_dict.copy()])
        t.daemon = True
        t.name = f"tm-deferred-{symbol}"
        t.start()
        logger.info(
            f"[TM] {symbol}: off-market hours ({_et_hour:02d}:xx ET) — "
            f"deferred trade_mole launch to 04:00 ET ({delay/3600:.1f}h from now)"
        )
        return

    _do_launch_trade_mole(completed_dict)


# ─── TSV writer ───────────────────────────────────────────────────────────────

_TSV_COLUMNS = [
    'Symbol', 'Tickers', 'ID', 'ArrivalTime', 'Headline', 'Author',
    'Float', 'Exchange', 'FinBERTCompletedAt',
    'positive', 'negative', 'neutral', 'sentiment_score', 'label',
    'nocoref_neutral_filter', 'nocoref_confidence_weighted',
    'nocoref_net_score', 'nocoref_top_k', 'nocoref_positional',
    'NoCorefCompletedAt',
    'Trigger',
]


def _append_to_tsv(completed_dict: dict) -> None:
    date_str  = datetime.now().strftime('%Y-%m-%d')
    file_path = os.path.join(OUTPUT_DIR, f"news_output_{date_str}.tsv")
    try:
        with _tsv_lock:
            file_exists = os.path.isfile(file_path)
            if file_exists:
                with open(file_path, 'r', encoding='utf-8') as f:
                    existing_header = f.readline().rstrip('\n').split('\t')
                if existing_header != _TSV_COLUMNS:
                    rotated = file_path.replace('.tsv', '_rotated.tsv')
                    os.rename(file_path, rotated)
                    logger.warning(
                        f"TSV header mismatch — rotated old file to {rotated}"
                    )
                    file_exists = False
            if not file_exists:
                os.makedirs(OUTPUT_DIR, exist_ok=True)
            with open(file_path, 'a', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=_TSV_COLUMNS,
                                        delimiter='\t', extrasaction='ignore')
                if not file_exists:
                    writer.writeheader()
                writer.writerow(completed_dict)
    except Exception as exc:
        logger.error(f"TSV write failed for id={completed_dict.get('ID')}: {exc}", exc_info=True)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _lookup_float(symbol: str):
    with _iti_df_lock:
        df = _iti_df
    if df is None or symbol not in df['Symbol'].values:
        return None
    val = df.loc[df['Symbol'] == symbol, 'Float_M'].iloc[0]
    return None if pd.isna(val) else val


def _lookup_author(news_id: str):
    obj = nw.get_news_object(f"id-{news_id}")
    if obj is None:
        return None
    return obj.get('author')


def _load_latest_iti_tsv() -> None:
    global _iti_df
    pattern = os.path.join(TM_ITI_DATA_DIR, 'nasdaq_symbols_data_priced_????-??-??.tsv')
    matches = sorted(glob.glob(pattern))
    if not matches:
        logger.warning(f"[ITI] No dated universe TSV found in {TM_ITI_DATA_DIR} — ITI lookups will use default")
        return
    path = matches[-1]
    try:
        df = pd.read_csv(path, sep='\t')
        with _iti_df_lock:
            _iti_df = df
        logger.info(f"[ITI] Loaded {path} ({len(df)} symbols)")
    except Exception as exc:
        logger.error(f"[ITI] Failed to load {path}: {exc}", exc_info=True)


def _lookup_baseline_iti(symbol: str) -> float:
    now_et = datetime.now(tz=ZoneInfo('America/New_York'))
    h, m = now_et.hour, now_et.minute
    is_rth = (h == 9 and m >= 30) or (10 <= h < 16)
    col = 'RTH_avgITI_sec' if is_rth else 'ETH_avgITI_sec'
    with _iti_df_lock:
        df = _iti_df
    if df is None or symbol not in df['Symbol'].values:
        logger.warning(f"[TM] {symbol}: ITI not found in universe — using default {TM_DEFAULT_BASELINE_ITI}s")
        return TM_DEFAULT_BASELINE_ITI
    val = df.loc[df['Symbol'] == symbol, col].iloc[0]
    if pd.isna(val):
        logger.warning(f"[TM] {symbol}: {col} is NaN — using default {TM_DEFAULT_BASELINE_ITI}s")
        return TM_DEFAULT_BASELINE_ITI
    return float(val)


def _iti_reload_worker(stop_event: threading.Event) -> None:
    global _last_iti_reload_date
    ET_TZ = ZoneInfo('America/New_York')
    while not stop_event.is_set():
        now = datetime.now(tz=ET_TZ)
        if now.hour >= 20 and now.date() != _last_iti_reload_date:
            logger.info("[ITI] 20:00+ ET — reloading latest universe TSV")
            _load_latest_iti_tsv()
            _last_iti_reload_date = now.date()
        stop_event.wait(timeout=60.0)


# ─── Per-ticker collector — runs on collect_worker thread ────────────────────

def _collect_and_log(news_dict: dict, tickers: list, symbol: str,
                     f_finbert, f_body) -> None:
    news_id = news_dict['ID']

    # 1) Headline FinBERT
    try:
        finbert_val = f_finbert.result(timeout=60)
        finbert_completed_at = datetime.now()
    except Exception as exc:
        logger.error(f"FinBERT-headliner error for id={news_id}: {exc}", exc_info=True)
        finbert_val = {}
        finbert_completed_at = None

    # 2) Build the headline portion of the row
    completed_dict = {
        'Symbol':             symbol,
        'Tickers':            json.dumps(tickers),
        'ID':                 news_id,
        'ArrivalTime':        news_dict['ArrivalTime'].replace(microsecond=0),
        'Headline':           news_dict['Headline'],
        'Author':             _lookup_author(news_id),
        'Float':              _lookup_float(symbol),
        'Exchange':           news_dict.get('exchange', ''),
        'FinBERTCompletedAt': finbert_completed_at,
        'positive':           finbert_val.get('positive'),
        'negative':           finbert_val.get('negative'),
        'neutral':            finbert_val.get('neutral'),
        'sentiment_score':    finbert_val.get('sentiment_score'),
        'label':              finbert_val.get('label'),
    }
    completed_dict['Trigger'] = evaluate_trigger(completed_dict)

    # 3) Launch trade_mole IMMEDIATELY — do NOT wait for the body pipeline.
    maybe_launch_trade_mole(completed_dict)

    # 4) Body pipeline — populate the 5 nocoref scores + timestamp
    try:
        body_result = f_body.result(timeout=BODY_FINBERT_TIMEOUT_SEC)
    except Exception as exc:
        logger.error(
            f"FinBERT-body error for id={news_id} sym={symbol}: {exc}",
            exc_info=True,
        )
        body_result = {}

    completed_dict.update(_compute_nocoref_scores(body_result, symbol))
    completed_dict['NoCorefCompletedAt'] = datetime.now() if body_result else None

    # 5) Write the TSV row
    _append_to_tsv(completed_dict)

    logger.info(f"Completed news_dict: {completed_dict}")
    print(f"\n{'='*60}")
    print(f"NEWS ITEM PROCESSED")
    print(f"  Symbol                       : {completed_dict['Symbol']}")
    print(f"  Tickers                      : {completed_dict['Tickers']}")
    print(f"  ID                           : {completed_dict['ID']}")
    print(f"  ArrivalTime                  : {completed_dict['ArrivalTime']}")
    print(f"  Headline                     : {completed_dict['Headline']}")
    print(f"  Author                       : {completed_dict['Author']}")
    print(f"  FinBERT label                : {completed_dict['label']}")
    print(f"  sentiment_score              : {completed_dict['sentiment_score']}")
    print(f"  positive                     : {completed_dict['positive']}")
    print(f"  negative                     : {completed_dict['negative']}")
    print(f"  neutral                      : {completed_dict['neutral']}")
    print(f"  Float                        : {completed_dict['Float']}")
    print(f"  Exchange                     : {completed_dict['Exchange']}")
    print(f"  FinBERTCompletedAt           : {completed_dict['FinBERTCompletedAt']}")
    print(f"  nocoref_neutral_filter       : {completed_dict['nocoref_neutral_filter']}")
    print(f"  nocoref_confidence_weighted  : {completed_dict['nocoref_confidence_weighted']}")
    print(f"  nocoref_net_score            : {completed_dict['nocoref_net_score']}")
    print(f"  nocoref_top_k                : {completed_dict['nocoref_top_k']}")
    print(f"  nocoref_positional           : {completed_dict['nocoref_positional']}")
    print(f"  NoCorefCompletedAt           : {completed_dict['NoCorefCompletedAt']}")
    print(f"  Trigger                      : {completed_dict['Trigger']}")
    print(f"{'='*60}\n")


# ─── Callback — invoked from NW4 background thread ───────────────────────────

def on_news_accepted(news_dict: dict) -> None:
    raw_symbol = news_dict['Symbol']
    news_id    = news_dict['ID']
    tickers    = [t.strip() for t in raw_symbol.split(',') if t.strip()]
    if not tickers:
        logger.warning(f"on_news_accepted: id={news_id} has no tickers — skipping")
        return

    logger.info(
        f"on_news_accepted triggered: id={news_id} tickers={tickers}"
    )

    f_finbert = _finbert_executor.submit(analyze_finbert, news_dict)
    f_body    = _body_executor.submit(_run_body_pipeline, news_dict)
    for symbol in tickers:
        _collect_executor.submit(
            _collect_and_log, news_dict, tickers, symbol, f_finbert, f_body,
        )


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    global _tm_pool

    logger.info("Orchestrator3.6 starting...")

    for d in (OUTPUT_DIR, TM_OUTPUT_DIR, TM_LOG_DIR):
        if d:
            os.makedirs(d, exist_ok=True)

    logger.info("Pre-loading FinBERT-headliner model...")
    load_model()
    logger.info("FinBERT-headliner model ready.")

    # Pre-warm body pipeline workers — guarantees no article ever pays the
    # cold-start penalty.
    logger.info(f"Pre-warming {BODY_FINBERT_WORKERS} FinBERT body pipeline worker(s)...")
    _futures_wait([_body_executor.submit(lambda: None)
                   for _ in range(BODY_FINBERT_WORKERS)])
    logger.info("All body pipeline workers ready.")

    _load_latest_iti_tsv()
    _load_excluded_strings(TM_EXCLUDED_STRINGS_FILE)

    # ── Spawn trade_mole manager pool (strict: aborts on READY timeout) ──────
    _tm_pool = TradeMolePool(
        client_ids=TM_CLIENT_IDS,
        slots_per_client=TM_SLOTS_PER_CLIENT,
        max_extensions=TM_MAX_EXTENSIONS,
        lifetime_sec=TM_LIFETIME_SEC,
        python_exec=TM_PYTHON,
        manager_script=TM_SCRIPT,
        host=TM_HOST,
        port=TM_PORT,
        output_dir=TM_OUTPUT_DIR,
        log_dir=TM_LOG_DIR,
    )
    try:
        _tm_pool.start()
    except RuntimeError as exc:
        logger.error(f"[TM] pool startup failed: {exc}")
        _tm_pool.shutdown()
        sys.exit(1)

    # Register callback BEFORE start() — no race window for missed items
    nw.register_callback(on_news_accepted)

    nw.start(
        universe_tsv=NW4_UNIVERSE_TSV,
        black_list=NW4_BLACK_LIST,
        blacklist_expiry_hours=NW4_BLACKLIST_EXPIRY_HOURS,
        api_keys=NW4_API_KEYS,
        log_dir=NW4_LOG_DIR,
        output_dir=NW4_OUTPUT_DIR,
        news_df_dir=NW4_NEWS_DF_DIR,
        blocked_dir=NW4_BLOCKED_DIR,
        accepted_dir=NW4_ACCEPTED_DIR,
        excluded_strings_file=NW4_EXCLUDED_STRINGS_FILE,
        priced_tsv=NW4_PRICED_TSV,
        reject_float_greater_then=NW4_REJECT_FLOAT_GT,
        reject_price_greater_then=NW4_REJECT_PRICE_GT,
        flush_interval_seconds=NW4_FLUSH_INTERVAL_SEC,
    )

    logger.info("NewsWatcher4 started. Waiting for news... (Ctrl+C to stop)")

    # ── Keep-alive ────────────────────────────────────────────────────────────
    _stop_event = threading.Event()

    _iti_thread = threading.Thread(
        target=_iti_reload_worker, args=(_stop_event,),
        daemon=True, name='iti-reload',
    )
    _iti_thread.start()

    def _handle_sigint(signum, frame):
        logger.info("Shutdown signal received — stopping...")
        _stop_event.set()

    signal.signal(signal.SIGINT,  _handle_sigint)
    signal.signal(signal.SIGTERM, _handle_sigint)

    try:
        _stop_event.wait()
    finally:
        # Shutdown order matters: stop NW first so no new news arrives,
        # then drain executors so in-flight collects finish (which may
        # call pool.launch), then shut down the pool, then exit.
        logger.info("Stopping NewsWatcher4...")
        try:
            nw.stop()
        except Exception:
            logger.exception("nw.stop() raised")

        logger.info("Shutting down executors...")
        _finbert_executor.shutdown(wait=True)
        _collect_executor.shutdown(wait=True)
        _body_executor.shutdown(wait=True)
        _tm_launch_executor.shutdown(wait=True)

        if _tm_pool is not None:
            _tm_pool.shutdown()

        logger.info("Orchestrator3.6 stopped.")


if __name__ == '__main__':
    main()
