import sys
import os
import csv
import json
import queue
import signal
import logging
import threading
import subprocess
import importlib.util as _ilu
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, wait as _futures_wait
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional
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
# Both modules import cleanly once their directory is on sys.path (above).
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

# ── Trade-mole trigger (pre-connected worker pool) ────────────────────────────
TM_SENTIMENT_SCORE_MIN = 0.7     # launch only if sentiment_score >  this
TM_FLOAT_MAX_M         = 50      # launch only if Float (millions) <  this

TM_TRADE_MOLE_SCRIPT  = '/home/tom/Documents/ibkr_scripts/N2/scripts/volume/trade_surge_mole/trade_mole_4.4.py'
TM_POOL_CLIENT_IDS    = list(range(330, 340))   # 10 clients: 330..339
TM_LIFETIME           = '10:00'  # mm:ss — sent to worker in pool command
TM_OUTPUT_DIR         = '/home/tom/Documents/ibkr_scripts/N2/scripts/orchestrator3/trade_mole_outputs'
TM_LOG_DIR            = '/home/tom/Documents/ibkr_scripts/N2/scripts/orchestrator3/trade_mole_logs'
TM_HOST               = '127.0.0.1'
TM_PORT               = 4001     # 4001=live GW  4002=paper GW  7496/7497=TWS
TM_PYTHON             = sys.executable   # same interpreter as Orchestrator
TM_DEFAULT_BASELINE_ITI = 44444.0    # fallback ITI (s) when symbol absent/NaN in universe
TM_ITI_DATA_DIR       = '/home/tom/Documents/ibkr_scripts/N2/scripts/universe_finder/data'

# Pool tuning
TM_POOL_READY_TIMEOUT_SEC = 15.0                 # informational; no hard kill
TM_POOL_RESPAWN_BACKOFF   = (2, 4, 8, 16, 60)    # seconds after Nth consecutive connect-fail
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
logger = logging.getLogger('Orchestrator3.8')

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

# Body pipeline is NOT thread-safe (shared spaCy nlp, ONNX session,
# TickerResolver cache). Each worker gets its own instance loaded once via the
# ThreadPoolExecutor initializer — guaranteed before any article arrives thanks
# to the pre-warm loop in main().
_body_local = threading.local()


def _init_body_worker() -> None:
    """ThreadPoolExecutor initializer: runs exactly once per worker thread.
    All heavy models (spaCy, ONNX FinBERT, SEC-EDGAR ticker map) are loaded
    here so no article ever pays the cold-start penalty.

    Note: each worker's pipeline holds its own TickerResolver. We deliberately
    do NOT call pipe.shutdown() on Orchestrator exit — the SEC EDGAR cache will
    rebuild from disk on next startup. Two threads racing on save_cache() is
    the only thing we'd risk, and the cost of not saving is negligible.
    """
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


# ─── Trade-mole worker pool ──────────────────────────────────────────────────
#
# Design: 10 long-lived trade_mole_4.3 subprocesses, each pre-connected to
# IBKR on a dedicated clientID (330..339). Each worker idles reading stdin
# until the orchestrator sends it one JSON command (symbol, baseline_iti,
# lifeTime_sec, output_dir, log_dir), then runs that single mole and exits.
# A per-slot reaper thread detects the exit and immediately spawns a fresh
# replacement worker on the same clientID — the pool size stays at 10 in
# steady state with brief 1–3s windows of N-1 during respawn.
#
# When all 10 are busy (state='running'), the dispatch() call drops the
# trigger and logs a warning. This is the user-chosen policy: we never
# fall back to spawning a non-pooled subprocess, and we never queue a
# trigger to wait — by the time a slot frees (≤ TM_LIFETIME), the surge
# we're trying to catch is long gone.
#
# Survivability: workers are spawned with start_new_session=True so they
# reparent to init when the orchestrator dies. Running moles always
# complete their lifeTime regardless of parent exit; idle workers exit
# cleanly on stdin EOF when the parent's pipe closes.

# Pool lifetime in seconds, derived once from TM_LIFETIME
def _parse_lifetime_sec(s: str) -> int:
    mm, ss = s.split(':')
    return int(mm) * 60 + int(ss)

_TM_LIFETIME_SEC = _parse_lifetime_sec(TM_LIFETIME)

# Module-level singleton; assigned in main() after universe TSV is loaded
# (TradeMolePool.dispatch / slot.commit read _lookup_baseline_iti which
# needs _iti_df, and _try_speculative_subscribe gates on _lookup_float).
_pool: Optional["TradeMolePool"] = None


class _PoolSlot:
    """One pre-connected worker. State machine (v3.8 — two-phase protocol):

       spawning → connecting → idle ─────────────────────────────────→ running → dead
                                ↑↓                                       ↑
                                ├─→ subscribed ─(commit)─────────────────┘
                                │       │
                                │       └─(abort)─→ abort_pending ──────┐
                                │                                       │
                                └───────────────────────────────────────┘
                                    (worker re-emits {"event":"ready"})

    Reader thread reads stdout:
      * First {"event":"ready",...} transitions 'connecting' → 'idle'.
      * Subsequent {"event":"ready",...} transitions 'abort_pending' → 'idle'
        (the worker recycled after an abort).
    Both edges push self onto the pool's _idle_queue.

    Reaper thread blocks on proc.wait(); on exit marks 'dead' and (unless
    pool is shutting down) spawns a fresh worker on the same clientID after
    an exponential backoff if the previous worker failed before reaching
    'idle'.
    """

    # Slot states. ``subscribed`` means we've sent {"event":"subscribe",...}
    # and reqMktData is live on the worker but no surge logic is running yet;
    # the caller still owns the slot and must follow up with commit() or
    # abort(). ``abort_pending`` means abort was sent and we're waiting for
    # the worker's {"event":"ready"} re-emission before flipping back to idle.
    _STATES = ('spawning', 'connecting', 'idle', 'subscribed',
               'running', 'abort_pending', 'dead')

    def __init__(self, client_id: int, pool: "TradeMolePool"):
        self.client_id = client_id
        self.pool = pool
        self.proc: Optional[subprocess.Popen] = None
        self.state: str = 'spawning'
        self.state_lock = threading.Lock()
        self.consecutive_failures = 0
        self.current_symbol: Optional[str] = None

    # ------------------------------------------------------------------
    # Spawn + reader + reaper
    # ------------------------------------------------------------------

    def spawn(self) -> None:
        """Popen the worker, start its reader + reaper threads. Non-blocking.
        Safe to call from any thread (only invoked by __init__-time loop and
        by the reaper after a worker exits)."""
        argv = [
            TM_PYTHON, TM_TRADE_MOLE_SCRIPT,
            '--pool-mode',
            '--clientID', str(self.client_id),
            '--host',     TM_HOST,
            '--port',     str(TM_PORT),
            '--log-dir',  TM_LOG_DIR,
        ]
        try:
            os.makedirs(TM_LOG_DIR, exist_ok=True)
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,                # line-buffered text streams
                start_new_session=True,   # detach from orch pgid (Ctrl+C immunity)
                close_fds=True,
            )
        except Exception as exc:
            logger.error(f"[TM] slot {self.client_id} spawn failed: {exc}", exc_info=True)
            with self.state_lock:
                self.state = 'dead'
                self.consecutive_failures += 1
            # Retry via Timer rather than recursing — keeps stack flat.
            backoff = self._next_backoff()
            t = threading.Timer(backoff, self.spawn)
            t.daemon = True
            t.start()
            return

        with self.state_lock:
            self.proc = proc
            self.state = 'connecting'
            self.current_symbol = None
        logger.info(f"[TM] slot {self.client_id} spawned pid={proc.pid}")

        threading.Thread(
            target=self._reader_thread, daemon=True,
            name=f"tm-reader-{self.client_id}",
        ).start()
        threading.Thread(
            target=self._reaper_thread, daemon=True,
            name=f"tm-reaper-{self.client_id}",
        ).start()

    def _next_backoff(self) -> float:
        """Pick the backoff for the (consecutive_failures-th) restart."""
        idx = min(max(self.consecutive_failures - 1, 0), len(TM_POOL_RESPAWN_BACKOFF) - 1)
        return float(TM_POOL_RESPAWN_BACKOFF[idx])

    def _reader_thread(self) -> None:
        """Drains worker stdout line-by-line. {"event":"ready",...} is the
        only message the worker emits on stdout.

          * First ready: 'connecting' → 'idle' (worker initial connect ack).
          * Subsequent ready: 'abort_pending' → 'idle' (worker recycled
            after an abort). The slot re-enters _idle_queue and becomes
            eligible for try_subscribe() again.

        Any other stdout content is logged at DEBUG (the worker's normal
        logs go to its log file in pool mode). EOF means the worker
        exited; the reaper handles state transitions there.
        """
        proc = self.proc
        if proc is None or proc.stdout is None:
            return
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    logger.debug(f"[TM] slot {self.client_id} non-JSON stdout: {line!r}")
                    continue
                if msg.get('event') == 'ready':
                    with self.state_lock:
                        prev = self.state
                        if self.state in ('connecting', 'abort_pending'):
                            self.state = 'idle'
                            if prev == 'connecting':
                                self.consecutive_failures = 0
                            ready = True
                        else:
                            ready = False
                    if ready:
                        self.pool._idle_queue.put(self)
                        logger.info(
                            f"[TM] pool slot {self.client_id} {prev}→idle "
                            f"(pid={proc.pid}, queue depth={self.pool._idle_queue.qsize()})"
                        )
                    else:
                        logger.debug(
                            f"[TM] slot {self.client_id}: ignoring ready "
                            f"in state={prev}"
                        )
                else:
                    logger.debug(f"[TM] slot {self.client_id} unexpected msg: {msg}")
        except Exception as exc:
            logger.debug(f"[TM] slot {self.client_id} reader exit: {exc}")

    def _reaper_thread(self) -> None:
        """Blocks on proc.wait(). On exit:
          * marks state='dead' (atomic snapshot of prev_state for diagnostics)
          * if exit happened while still 'connecting' (READY never arrived),
            increments consecutive_failures so the next spawn backs off.
          * if pool is shutting down, no respawn.
          * otherwise spawn a replacement on the same clientID.
        """
        proc = self.proc
        if proc is None:
            return
        try:
            rc = proc.wait()
        except Exception as exc:
            logger.warning(f"[TM] slot {self.client_id} wait raised: {exc}")
            rc = -1
        with self.state_lock:
            prev_state = self.state
            sym = self.current_symbol
            self.state = 'dead'
            self.current_symbol = None
            if prev_state == 'connecting':
                self.consecutive_failures += 1
            elif prev_state in ('idle', 'subscribed', 'running', 'abort_pending'):
                # Worker reached READY at least once — reset failure counter.
                self.consecutive_failures = 0
        logger.info(
            f"[TM] slot {self.client_id} exited rc={rc} prev_state={prev_state} "
            f"sym={sym} consecutive_failures={self.consecutive_failures}"
        )
        if self.pool._shutdown_event.is_set():
            logger.info(f"[TM] slot {self.client_id}: pool shutting down, no respawn")
            return
        if self.consecutive_failures > 0:
            backoff = self._next_backoff()
            logger.warning(
                f"[TM] slot {self.client_id}: failure #{self.consecutive_failures} — "
                f"respawn in {backoff:.0f}s"
            )
            if self.pool._shutdown_event.wait(timeout=backoff):
                return
        self.spawn()

    # ------------------------------------------------------------------
    # Two-phase dispatch (v3.8): subscribe → (commit | abort)
    # ------------------------------------------------------------------

    def _write_cmd(self, cmd: dict) -> bool:
        """Serialize ``cmd`` as one JSON line on the worker's stdin and flush.
        Returns True on success; on BrokenPipe/OSError marks the slot dead
        (the reaper will respawn) and returns False."""
        if self.proc is None or self.proc.stdin is None:
            return False
        try:
            self.proc.stdin.write(json.dumps(cmd) + '\n')
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            logger.warning(
                f"[TM] slot {self.client_id}: stdin write failed ({exc}) "
                f"— marking dead, reaper will respawn"
            )
            with self.state_lock:
                self.state = 'dead'
            return False
        return True

    def subscribe(self, symbol: str) -> bool:
        """Send {"event":"subscribe","symbol":…} to the worker. Caller MUST
        eventually follow up with commit() or abort() — leaving a slot in
        'subscribed' state pays for an open IBKR mkt-data subscription until
        the worker's POOL_SUBSCRIBED_TIMEOUT_SEC fires.

        Returns True on success, False if the slot was no longer idle (race
        with reaper) or the write failed."""
        with self.state_lock:
            if self.state != 'idle' or self.proc is None or self.proc.stdin is None:
                return False
            self.state = 'subscribed'
            self.current_symbol = symbol
        if not self._write_cmd({'event': 'subscribe', 'symbol': symbol}):
            return False
        logger.info(
            f"[TM] subscribe {symbol} → slot {self.client_id} pid={self.proc.pid} "
            f"(speculative; awaiting commit/abort)"
        )
        return True

    def commit(self, baseline_iti: float, completed_dict: dict) -> bool:
        """Send {"event":"commit",…} to a slot already in 'subscribed' state.
        The worker flips its _committed gate, applies the baseline, and runs
        the standard surge loop until lifetime expires. Returns True on
        success, False if the slot wasn't actually subscribed (race) or the
        write failed.

        After a successful commit the slot is in 'running' and will not be
        recycled — the process exits at lifetime end and the reaper spawns
        a replacement, same as the v3.7 dispatch behavior."""
        with self.state_lock:
            if (self.state != 'subscribed'
                    or self.proc is None or self.proc.stdin is None):
                return False
            self.state = 'running'
            symbol = self.current_symbol
        if not self._write_cmd({
            'event':         'commit',
            'baseline_iti':  baseline_iti,
            'lifeTime_sec':  _TM_LIFETIME_SEC,
            'output_dir':    TM_OUTPUT_DIR,
        }):
            return False
        sentiment = completed_dict.get('sentiment_score')
        float_m   = completed_dict.get('Float')
        logger.info(
            f"[TM] committed {symbol} → slot {self.client_id} pid={self.proc.pid} "
            f"sentiment={sentiment:.4f} float={float_m} baseline_iti={baseline_iti:.2f}s "
            f"lifetime={_TM_LIFETIME_SEC}s"
        )
        return True

    def abort(self) -> bool:
        """Send {"event":"abort"} to a slot in 'subscribed' state. Worker
        cancels reqMktData, resets per-session state, and re-emits
        {"event":"ready"} — at which point the reader thread flips us
        from 'abort_pending' back to 'idle' and re-enqueues us.

        Returns True if the abort was issued, False if the slot wasn't in
        'subscribed' state or the write failed (in which case the slot is
        marked dead and the reaper will respawn)."""
        with self.state_lock:
            if (self.state != 'subscribed'
                    or self.proc is None or self.proc.stdin is None):
                return False
            self.state = 'abort_pending'
            prev_symbol = self.current_symbol
            self.current_symbol = None
        if not self._write_cmd({'event': 'abort'}):
            return False
        logger.info(
            f"[TM] aborted {prev_symbol} → slot {self.client_id} pid={self.proc.pid} "
            f"(awaiting worker recycle)"
        )
        return True


class TradeMolePool:
    """Manages the 10-worker pool. Lifecycle:
       __init__   → spawn() each slot in parallel (non-blocking)
       dispatch() → pop one idle slot, send it the JSON command
       shutdown() → set shutdown_event, close stdin on idle workers (they
                    exit on EOF), let running workers run to completion as
                    orphans (start_new_session=True detaches them)."""

    def __init__(self, client_ids):
        self.client_ids = list(client_ids)
        self.slots = [_PoolSlot(cid, self) for cid in self.client_ids]
        # SimpleQueue: thread-safe FIFO; we don't need bounded size.
        self._idle_queue: queue.SimpleQueue = queue.SimpleQueue()
        self._shutdown_event = threading.Event()
        for slot in self.slots:
            slot.spawn()

    @property
    def size(self) -> int:
        return len(self.slots)

    def _pop_idle_slot(self) -> Optional["_PoolSlot"]:
        """Drain stale entries (slots that were 'idle' when enqueued but
        died or transitioned before dispatch) from the front of the queue
        and return the first slot whose state is actually 'idle'. Returns
        None if no idle slot is available."""
        while True:
            try:
                candidate = self._idle_queue.get_nowait()
            except queue.Empty:
                return None
            with candidate.state_lock:
                if candidate.state == 'idle':
                    return candidate
            logger.debug(
                f"[TM] _pop_idle_slot: skipping slot {candidate.client_id} "
                f"(state={candidate.state})"
            )

    def try_subscribe(self, symbol: str) -> Optional["_PoolSlot"]:
        """Best-effort: pop an idle slot and send it a SUBSCRIBE command for
        ``symbol``. Returns the slot handle on success (caller MUST follow up
        with slot.commit() or slot.abort()) or None if no slot was available
        or the subscribe write failed.

        Called speculatively from on_news_accepted, in parallel with the
        FinBERT-headliner job, so that reqMktData is already in flight by
        the time the trigger decision is known."""
        if self._shutdown_event.is_set():
            return None
        while True:
            slot = self._pop_idle_slot()
            if slot is None:
                return None
            if slot.subscribe(symbol):
                return slot
            # subscribe() failed (race or broken pipe) — try the next slot.
            logger.debug(
                f"[TM] try_subscribe: slot {slot.client_id} subscribe failed, retrying"
            )

    def dispatch(self, completed_dict: dict) -> None:
        """Standard (non-speculative) path: acquire an idle slot, issue
        SUBSCRIBE then immediately COMMIT. Used when on_news_accepted didn't
        pre-reserve a slot for this ticker (cheap-gates failed at PR-arrival
        time, the pool was empty, or this is the second ticker in a
        multi-ticker fan-out).

        On the worker side the two events are processed back-to-back in
        microseconds — there's no first-tick latency advantage over the
        v3.7 one-shot path, just neutral behavior. If the pool is empty the
        trigger is dropped (same policy as v3.7)."""
        if self._shutdown_event.is_set():
            return
        symbol = completed_dict['Symbol']
        sentiment = completed_dict.get('sentiment_score')
        float_m   = completed_dict.get('Float')

        slot = self.try_subscribe(symbol)
        if slot is None:
            logger.warning(
                f"[TM] {symbol}: all {self.size} pool clients busy — dropping trigger "
                f"(sentiment={sentiment} float={float_m})"
            )
            return

        baseline_iti = _lookup_baseline_iti(symbol)
        if not slot.commit(baseline_iti, completed_dict):
            logger.warning(
                f"[TM] {symbol}: slot {slot.client_id} commit failed (race) — aborting"
            )
            # commit() left state in 'subscribed' on race; try to recycle.
            slot.abort()

    def shutdown(self) -> None:
        """Signal reapers to stop respawning; close stdin on workers that
        haven't started surge collection yet so they exit on EOF.

          * idle / subscribed / abort_pending: close stdin. The v4.4 worker
            interprets EOF in each of these states as "abort and exit"
            (subscribed) or "clean exit" (idle), so reqMktData is properly
            cancelled and no orphan subscription leaks.
          * running: leave alone — the worker is mid-surge, detached
            (start_new_session=True), and will complete its lifeTime as an
            orphan, writing its CSV as usual.
        """
        self._shutdown_event.set()
        for slot in self.slots:
            with slot.state_lock:
                state = slot.state
                proc = slot.proc
                sym = slot.current_symbol
            if state in ('idle', 'subscribed', 'abort_pending') and proc is not None and proc.stdin is not None:
                try:
                    proc.stdin.close()
                    logger.info(
                        f"[TM] slot {slot.client_id}: stdin closed "
                        f"(state={state} sym={sym} — worker exits on EOF)"
                    )
                except Exception as exc:
                    logger.debug(f"[TM] slot {slot.client_id}: stdin close raised: {exc}")
            elif state == 'running':
                logger.info(
                    f"[TM] slot {slot.client_id}: still running {sym} "
                    f"— detaching (will complete lifetime as orphan)"
                )


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
    """Run the noCoref body pipeline on news_dict. Returns the full result dict,
    or {} if the body is missing/empty or any exception is raised.

    A shallow-copied dict is passed to the pipeline with a `tickers` list
    injected (derived from the comma-joined Symbol). The pipeline's NER
    fallback uses `tickers` as allowed_tickers when SEC EDGAR fails to resolve,
    ensuring every fan-out ticker has a chance to be scored.
    """
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
        # FIELD_NAME missing/empty — defensive (we already returned above).
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
    """Run the 5 neutral-management aggregations for `symbol` against the
    body pipeline's per-sentence FinBERT output. Returns a dict with 5 float
    keys (rounded to 4 decimals) or all-None on any missing data."""
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
    """Returns 'YES' if all trade_mole conditions are met, else 'NO:cond1,cond2,...'."""
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


# ─── Trade-mole launcher ──────────────────────────────────────────────────────

def _seconds_until_market_open() -> float:
    """Return seconds until 04:00 ET (start of pre-market window)."""
    now_et = datetime.now(tz=ZoneInfo('America/New_York'))
    target = now_et.replace(hour=4, minute=0, second=0, microsecond=0)
    if now_et >= target:
        target += timedelta(days=1)
    return (target - now_et).total_seconds()


def _do_launch_trade_mole(completed_dict: dict) -> None:
    """Dispatch to the persistent worker pool. Called immediately or by a
    deferred Timer (off-hours). Replaces the 3.5 subprocess.Popen path."""
    if _pool is None:
        logger.error(
            f"[TM] {completed_dict.get('Symbol')}: pool not initialized "
            "— cannot dispatch (this should never happen post-startup)"
        )
        return
    _pool.dispatch(completed_dict)


def maybe_launch_trade_mole(completed_dict: dict) -> None:
    """Fire-and-forget dispatch to the worker pool if trigger conditions are
    met for this single ticker. Off-hours triggers are deferred to 04:00 ET
    via threading.Timer — they do NOT consume a pool slot while waiting."""
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
            f"deferred dispatch to 04:00 ET ({delay/3600:.1f}h from now)"
        )
        return

    _do_launch_trade_mole(completed_dict)


# ─── Speculative subscribe (v3.8) ─────────────────────────────────────────────

def _try_speculative_subscribe(symbol: str, news_dict: dict) -> Optional["_PoolSlot"]:
    """Best-effort: reserve a pool slot for ``symbol`` at PR-arrival time
    (before FinBERT-headliner has finished) by sending it the SUBSCRIBE
    command. Returns the slot handle (caller MUST eventually call
    slot.commit() or slot.abort()) or None if any cheap pre-gate failed or
    no idle slot was available.

    The pre-gates here are intentionally identical to the corresponding
    early-returns in maybe_launch_trade_mole — if any of them would block
    the trigger from firing, there's no point holding a slot open. We do
    NOT yet know the sentiment_score (FinBERT is still running), so the
    only signal-based gate deferred to commit time is sentiment.

    Gates (all sync, < 1 ms total):
      * on-hours: ET hour ∈ [4, 20). Off-hours triggers take the deferred
        Timer path; speculative subscribe would just burn a slot for
        hours.
      * no excluded-string match in the headline.
      * Float < TM_FLOAT_MAX_M (dict lookup against the universe TSV).
        None means the symbol isn't in the universe — fall through to the
        existing slow path rather than guess.
    """
    if _pool is None:
        return None

    et_hour = datetime.now(tz=ZoneInfo('America/New_York')).hour
    if et_hour >= 20 or et_hour < 4:
        return None

    headline = news_dict.get('Headline') or ''
    if any(s.lower() in headline.lower() for s in _excluded_strings):
        return None

    float_m = _lookup_float(symbol)
    if float_m is None or float_m >= TM_FLOAT_MAX_M:
        return None

    slot = _pool.try_subscribe(symbol)
    if slot is None:
        # Pool busy — fall through to slow path. Not a warning; the slow
        # path is fully functional, just ~200–600 ms slower on first tick.
        logger.info(
            f"[TM] {symbol}: speculative subscribe skipped (pool busy) "
            f"— will fall back to subscribe-at-commit on trigger"
        )
    return slot


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
    """Appends one row (one ticker) to the daily TSV output file. Thread-safe."""
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
    """Pull Float_M for `symbol` from the daily universe TSV. Returns None if absent."""
    with _iti_df_lock:
        df = _iti_df
    if df is None or symbol not in df['Symbol'].values:
        return None
    val = df.loc[df['Symbol'] == symbol, 'Float_M'].iloc[0]
    return None if pd.isna(val) else val


def _lookup_author(news_id: str):
    """Pull `author` from NW4's in-memory accepted-objects store. Returns None
    if the article has already been pruned (post-flush) or is missing."""
    obj = nw.get_news_object(f"id-{news_id}")
    if obj is None:
        return None
    return obj.get('author')


def _load_latest_iti_tsv() -> None:
    """Load the most recent nasdaq_symbols_data_priced_YYYY-MM-DD.tsv into _iti_df."""
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
    """Return the appropriate baseline ITI for symbol based on current ET time."""
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
    """Background thread: reloads the latest universe TSV once per day at/after 20:00 ET."""
    global _last_iti_reload_date
    ET_TZ = ZoneInfo('America/New_York')
    while not stop_event.is_set():
        now = datetime.now(tz=ET_TZ)
        if now.hour >= 20 and now.date() != _last_iti_reload_date:
            logger.info("[ITI] 20:00+ ET — reloading latest universe TSV")
            _load_latest_iti_tsv()
            _last_iti_reload_date = now.date()
        stop_event.wait(timeout=60.0)


# ─── Pool dispatch resolver (v3.8) ───────────────────────────────────────────

def _resolve_pool_dispatch(completed_dict: dict,
                           speculative_slot: Optional["_PoolSlot"]) -> None:
    """Decide what to do with the (already-evaluated) trigger now that we
    know FinBERT's sentiment_score:

      * If a ``speculative_slot`` was reserved at PR-arrival time, branch
        on Trigger + on-hours and either commit, abort, or abort+defer.
      * If no slot was reserved, fall through to the v3.7 maybe_launch_trade_mole
        path (which itself handles all gates plus the off-hours Timer)."""
    symbol  = completed_dict['Symbol']
    trigger = completed_dict.get('Trigger', '')

    if speculative_slot is None:
        # No slot held — standard slow path (subscribe-at-commit inside dispatch).
        maybe_launch_trade_mole(completed_dict)
        return

    # We hold a slot whose worker is already streaming ticks for `symbol`.
    if trigger != 'YES':
        # Sentiment / float / excluded-string check failed at trigger time.
        # Release the slot so it can serve another article.
        speculative_slot.abort()
        return

    # Trigger YES: re-check on-hours in case FinBERT pushed us past 20:00 ET.
    et_hour = datetime.now(tz=ZoneInfo('America/New_York')).hour
    if et_hour >= 20 or et_hour < 4:
        logger.info(
            f"[TM] {symbol}: crossed into off-hours during FinBERT — "
            f"aborting speculative slot and deferring to 04:00 ET"
        )
        speculative_slot.abort()
        # Reuse maybe_launch_trade_mole's off-hours Timer (it'll re-run all
        # the gates anyway — sentiment / float / excluded — but those have
        # already passed, so it'll just schedule the deferred dispatch).
        maybe_launch_trade_mole(completed_dict)
        return

    # On-hours, trigger YES, slot ready — commit.
    baseline_iti = _lookup_baseline_iti(symbol)
    if not speculative_slot.commit(baseline_iti, completed_dict):
        # commit() returned False — slot died between subscribe and commit
        # (reaper will respawn). Fall back to standard dispatch so the
        # trigger isn't lost.
        logger.warning(
            f"[TM] {symbol}: speculative commit failed — falling back to standard dispatch"
        )
        _do_launch_trade_mole(completed_dict)


# ─── Per-ticker collector — runs on collect_worker thread ────────────────────

def _collect_and_log(news_dict: dict, tickers: list, symbol: str,
                     f_finbert, f_body,
                     speculative_slot: Optional["_PoolSlot"] = None) -> None:
    """Resolves headline FinBERT first, evaluates trigger and dispatches to
    the pool IMMEDIATELY, then resolves the body future and writes the TSV
    row with all 5 nocoref scores + NoCorefCompletedAt.

    The body pipeline can take seconds-to-minutes for long press releases;
    decoupling pool dispatch from the TSV write keeps order execution
    fast while still landing the body enrichment in news_output_*.tsv.

    v3.8: if ``speculative_slot`` was reserved at PR-arrival time, the
    branching at trigger-evaluation time changes:
      * Trigger YES + on-hours: slot.commit() (worker tick stream is
        already warm — surge logic starts within ~5–20 ms).
      * Trigger NO: slot.abort() (worker cancels reqMktData and recycles).
      * Trigger YES + crossed-off-hours during FinBERT: slot.abort() +
        fall through to maybe_launch_trade_mole (which schedules the
        existing off-hours Timer).
    """
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
        'ArrivalTime':        news_dict['ArrivalTime'].strftime('%Y-%m-%d %H:%M:%S') + f".{news_dict['ArrivalTime'].microsecond // 1000:03d}",
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

    # 3) Dispatch to pool IMMEDIATELY — do NOT wait for the body pipeline.
    # Same ordering as 3.5/3.6: trade_mole launch must precede the body wait
    # so order execution isn't delayed by FinBERT body processing.
    _resolve_pool_dispatch(completed_dict, speculative_slot)

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
    # NoCorefCompletedAt is stamped on any successful pipeline run, even when
    # the per-ticker block is empty (NER + allowed_tickers fallback both
    # failed). None means we didn't run a pipeline at all (empty body or error).
    completed_dict['NoCorefCompletedAt'] = datetime.now() if body_result else None

    # 5) Write the TSV row (now populated with all 21 columns)
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
    """
    Invoked by NewsWatcher4 for every article that passes all filters.

    NW4's `Symbol` field is comma-joined for multi-ticker articles (up to 2).
    Strategy: fan-out per ticker. FinBERT-headliner and the body pipeline each
    run **once per article**; their futures are shared by every ticker's
    _collect_and_log task.

    v3.8: in addition to submitting the analysis futures, attempt one
    *speculative* SUBSCRIBE against the pool for the first ticker only
    (per design decision Q3). The cheap sync gates inside
    ``_try_speculative_subscribe`` (Float < 50M, no excluded string, on-hours)
    knock out most certain-fail tickers before we burn a slot. The second
    ticker — if any — falls through to the standard subscribe-at-commit path
    inside _collect_and_log → _resolve_pool_dispatch → maybe_launch_trade_mole.
    """
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

    # Speculative subscribe for the first ticker only. Runs in parallel with
    # FinBERT-headliner so that reqMktData is already in flight by the time
    # the trigger decision is made (~50–200 ms later).
    speculative_slot = _try_speculative_subscribe(tickers[0], news_dict)

    for i, symbol in enumerate(tickers):
        slot = speculative_slot if i == 0 else None
        _collect_executor.submit(
            _collect_and_log, news_dict, tickers, symbol, f_finbert, f_body, slot,
        )


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    global _pool
    logger.info("Orchestrator3.8 starting...")

    for d in (OUTPUT_DIR, TM_OUTPUT_DIR, TM_LOG_DIR):
        if d:
            os.makedirs(d, exist_ok=True)

    logger.info("Pre-loading FinBERT-headliner model...")
    load_model()
    logger.info("FinBERT-headliner model ready.")

    # Pre-warm body pipeline workers: submit N no-op tasks and wait. Each
    # worker thread runs _init_body_worker on first task pickup, so by the
    # time _futures_wait returns, every worker has its FinBERTBodyPipeline
    # fully loaded. This guarantees no article ever pays the cold-start cost.
    logger.info(f"Pre-warming {BODY_FINBERT_WORKERS} FinBERT body pipeline worker(s)...")
    _futures_wait([_body_executor.submit(lambda: None)
                   for _ in range(BODY_FINBERT_WORKERS)])
    logger.info("All body pipeline workers ready.")

    # Universe TSV first — TradeMolePool.dispatch / slot.commit use
    # _lookup_baseline_iti, and _try_speculative_subscribe uses _lookup_float.
    _load_latest_iti_tsv()
    _load_excluded_strings(TM_EXCLUDED_STRINGS_FILE)

    # Spawn the trade-mole worker pool. Non-blocking — workers connect in
    # parallel with the rest of startup. The first news event may arrive
    # before all 10 are READY; that's OK (dispatch logs a drop warning if so).
    logger.info(
        f"[TM] Spawning trade-mole pool: {len(TM_POOL_CLIENT_IDS)} workers "
        f"on clientIDs {TM_POOL_CLIENT_IDS} via {TM_TRADE_MOLE_SCRIPT}"
    )
    _pool = TradeMolePool(TM_POOL_CLIENT_IDS)

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

    # Register AFTER nw.start() so Orchestrator3.8's handlers override NW4's
    signal.signal(signal.SIGINT,  _handle_sigint)
    signal.signal(signal.SIGTERM, _handle_sigint)

    try:
        _stop_event.wait()
    finally:
        logger.info("Shutting down executors...")
        _finbert_executor.shutdown(wait=True)
        _collect_executor.shutdown(wait=True)
        _body_executor.shutdown(wait=True)
        logger.info("Shutting down trade-mole pool (idle workers exit on stdin EOF; running workers detach)...")
        if _pool is not None:
            _pool.shutdown()
        logger.info("Stopping NewsWatcher4...")
        nw.stop()
        logger.info("Orchestrator3.8 stopped.")


if __name__ == '__main__':
    main()
