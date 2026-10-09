#!/usr/bin/env python
"""
o55_fm3_lane.py -- Orchestrator5.5's FM3 SHADOW lane: the FM3 PR-text scorer, both twins,
in ONE low-priority worker subprocess. Logged only -- no rule may read FM3 in 5.5.

    /home/tom/venv/bin/python o55_fm3_lane.py goldens          # (re)write bench/o55_fm3_goldens.json
    /home/tom/venv/bin/python o55_fm3_lane.py check [--n 200]  # lane + goldens + latency, then exit

THE TWINS (tools/keyword/model-FM3/FM3_trained_on_PRstats-2026-05-04to09-24/)
  R1/DEVfit  trained on 05-04..08-27, tested ONCE on 08-31..09-24 (model-FM3/reports/
             FM3_TEST.md) -- the honest one.  -> FM3_bd_ge50 FM3_bd_reg FM3_hd_ge50 FM3_hd_reg
  R1_FT      the same recipe on every labelled row (a byte copy of R1/ALLfit) -- no held-out
             score and its own scale.        -> the same four names + "_FT"
  ⚠ RANKING scores (logits, z-averages, asinh-%), not probabilities; they can be negative.

WHY ITS OWN SUBPROCESS, AFTER THE DECISION, ONLY WHEN QUIET
  * FM3's lexical parts are GIL-bound Python (sklearn vectorizers, a few ms per twin). In
    the orchestrator they would contend with the collectors and the engine's threads.
  * nice 19 on the orchestrator's CPUs: its threads get a CPU first, FM3 takes what is left.
  * Orchestrator5.5 submits the job AFTER STEP-1b and its rows wait in a writer thread,
    never in a collector.
  * THE QUIET GATE (Fm3Lane): a job starts only once no PR has entered scoring for
    FM3_QUIET_MS and the engine queue is empty (max hold FM3_MAX_HOLD_S). Without it a
    burst's later PRs shared CPU cores with the FM3 work of its first ones (bench,
    2026-09-27: burst max 168 -> 254 ms).

REUSE, NOT A COPY: o54_engine.ProcessEngine (priority queue, feeder thread, hung-task
timeout, respawn with backoff) and o54_engine._WorkerProc (socketpair protocol,
PR_SET_PDEATHSIG, the ready handshake). Only the worker command and start() differ;
o54_engine.py is not modified.

THE PRIORITY WORKER (--prio; Fm3PrioModels / Fm3PrioLane) -- since 2026-10-07 Orchestrator5.5's
gate reads FM3_bd_ge50_FT and FM3_hd_reg_FT (FM3P_COLS), so those scores are needed BEFORE the
decision. A second, small worker scores only them, in one task: the R1_FT card's FM3_bd_ge50
artifact (a "zavg" of two TF-IDF lexical models on the body_200 text; no ONNX) and its
FM3_hd_reg artifact (a lexical model on the headline text), ~3-4 ms. Unlike the shadow lane it runs at nice 0, has NO
quiet gate, and is submitted at reception (kind 'fm3p', PRIO_TRADEABLE, 5 s task timeout). It is
the same code path as the shadow lane's FM3_*_FT -- round(_score(art, texts), 6) with
texts = texts_for(headline, body200=body_200), one texts dict for both (each artifact reads its
own channel, bd or hd) -- so the numbers are identical
(`check-prio` asserts it row by row). The shadow lane and its worker command are unchanged.

INPUT = (headline, body_200): the TSV Headline cell and Orchestrator5.5's _prep_body_200
string, body_prep.prep_body(clean_inline_text(body_raw), Headline, 200) -- the exact text
FM3 was trained and TEST-scored on (model-FM3/CLAUDE.md, "Protocol").
"""

import argparse
import csv
import glob
import json
import logging
import math
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent            # orchestrator5/
SCRIPTS_DIR = HERE.parent                         # N2/scripts
sys.path.insert(0, str(HERE))
import o54_engine as o54                          # noqa: E402

log = logging.getLogger('o55_fm3_lane')

FM3_DIR = SCRIPTS_DIR / "tools" / "keyword" / "model-FM3"
FM3_RELEASE = FM3_DIR / "FM3_trained_on_PRstats-2026-05-04to09-24"
FM3_TWINS = (("DEV", FM3_RELEASE / "R1" / "DEVfit"),
             ("FT", FM3_RELEASE / "R1_FT"))
FM3_OUTPUTS = ("FM3_bd_ge50", "FM3_bd_reg", "FM3_hd_ge50", "FM3_hd_reg")
FM3_COLS = FM3_OUTPUTS + tuple(o + "_FT" for o in FM3_OUTPUTS)    # the 8 TSV score columns
GOLDENS_FILE = HERE / "bench" / "o55_fm3_goldens.json"
TABLES_DIR = HERE / "tables"

FM3_NICE = 19
FM3_TASK_TIMEOUT_S = 30.0          # one job this long = hung worker -> killed + respawned
FM3_MAX_HOLD_S = 20.0              # the quiet gate never holds one job longer than this
FM3P_NICE = 0                      # the priority worker: a decision waits on it
FM3P_TASK_TIMEOUT_S = 5.0          # ~3 ms job; 5 s of nothing = hung worker -> respawn
FM3P_COLS = ("FM3_bd_ge50_FT", "FM3_hd_reg_FT")   # what the priority worker scores (gate inputs)
PRIO_FM3 = o54.PRIO_CORPUS         # one worker, FIFO: every job is post-decision anyway
_WARM_HD = "Acme Corp announces FDA approval of its lead drug candidate"
_WARM_BD = "Acme Corp today announced that the FDA approved its lead drug candidate."


# ─── The models (worker side; also this process for `goldens`) ────────────────

class Fm3Models:
    """Both twins, loaded through model-FM3/fm3_scorer.py (the serving path whose TEST
    parity was checked: max |diff| 8.4e-07)."""

    def __init__(self):
        self.mod = None
        self.scorers = []
        self.load_s = None

    def load(self):
        t0 = time.perf_counter()
        self.mod = o54.load_by_path("o55_fm3_scorer", FM3_DIR / "fm3_scorer.py", register=True)
        self.scorers = [self.mod.FM3Scorer(d) for _tag, d in FM3_TWINS]
        got = tuple(n for s in self.scorers for n in s.out_names if not n.startswith("FM3_ms"))
        if got != FM3_COLS:
            raise RuntimeError(f"FM3 twins emit {got}, expected {FM3_COLS} — wrong model "
                               f"dir or card suffix")
        self.load_s = round(time.perf_counter() - t0, 1)
        self.run(_WARM_HD, _WARM_BD)               # first-call costs off the live path
        return self

    def run(self, headline: str, body_200: str) -> dict:
        t0 = time.perf_counter()
        out = {}
        for s in self.scorers:
            r = s.score(headline or "", body200=body_200 or "")
            out.update({k: v for k, v in r.items() if not k.startswith("FM3_ms")})
        out['_ms'] = round((time.perf_counter() - t0) * 1000.0, 1)
        out['_done_epoch'] = time.time()
        return out

    def body_200(self, headline: str, body_cell: str) -> str:
        """Orchestrator5.5's _prep_body_200 for a TSV Body cell (already clean_inline_text)."""
        return self.mod.texts_for(headline or "", raw_body=body_cell or "")['bd']

    def compute_goldens(self, probes) -> list:
        return [[self.run(p['headline'], p['body_200'])[c] for c in FM3_COLS] for p in probes]

    def self_check(self, path: Path = GOLDENS_FILE, required: bool = True) -> str:
        """Exact equality (outputs are rounded to 6 dp) with the goldens file."""
        if not Path(path).is_file():
            if required:
                raise RuntimeError(f"FM3 goldens missing: {path} — run "
                                   f"`o55_fm3_lane.py goldens` first")
            return "skipped (no goldens file)"
        g = json.loads(Path(path).read_text(encoding='utf-8'))
        if list(g.get('cols') or []) != list(FM3_COLS):
            raise RuntimeError(f"{path} was written for other columns — regenerate it")
        mine = self.compute_goldens(g['probes'])
        bad = [f"probe {i} {c}: {a} != {b}"
               for i, (row, want) in enumerate(zip(mine, g['outputs']))
               for c, a, b in zip(FM3_COLS, row, want) if a != b]
        if bad:
            raise RuntimeError(f"FM3 goldens self-check FAILED ({len(bad)} mismatch(es)): "
                               + ' | '.join(bad[:6]))
        return f"ok ({len(mine)} probes x {len(FM3_COLS)})"


class Fm3PrioModels:
    """ONLY FM3_bd_ge50_FT + FM3_hd_reg_FT: the R1_FT card's FM3_bd_ge50 and FM3_hd_reg
    artifacts, loaded and scored exactly as FM3Scorer does (same card lookup, same
    _init_runtime, same round(.., 6), one texts dict; each artifact reads its own channel)."""
    OUT = "FM3_bd_ge50_FT"                       # kept: the first output
    OUTS = FM3P_COLS
    _BASE = ("FM3_bd_ge50", "FM3_hd_reg")        # card output names, in OUTS order

    def __init__(self):
        self.mod = None
        self.arts = {}
        self.load_s = None

    def load(self):
        import joblib
        t0 = time.perf_counter()
        self.mod = o54.load_by_path("o55_fm3_scorer", FM3_DIR / "fm3_scorer.py", register=True)
        d = dict(FM3_TWINS)["FT"]
        card_p = d / "card.json"
        card = json.loads((card_p if card_p.is_file() else d.parent / "card.json").read_text())
        suffix = card.get("suffix", "")
        for base, out in zip(self._BASE, self.OUTS):
            if base + suffix != out:
                raise RuntimeError(f"R1_FT card suffix {suffix!r}: {base + suffix} != "
                                   f"{out} — wrong model dir or card")
            art = joblib.load(d / card["outputs"][base]["file"])
            self.mod._init_runtime(art, d)
            self.arts[out] = art
        self.mod._prep()
        self.load_s = round(time.perf_counter() - t0, 1)
        self.run(_WARM_HD, _WARM_BD)               # first-call costs off the live path
        return self

    def run(self, headline: str, body_200: str) -> dict:
        t0 = time.perf_counter()
        texts = self.mod.texts_for(headline or "", body200=body_200 or "")
        out = {o: round(self.mod._score(self.arts[o], texts), 6) for o in self.OUTS}
        out['_ms'] = round((time.perf_counter() - t0) * 1000.0, 1)
        out['_done_epoch'] = time.time()
        return out

    def self_check(self, path: Path = GOLDENS_FILE, required: bool = True) -> str:
        """Exact equality with the FM3P_COLS columns of the shadow lane's goldens."""
        if not Path(path).is_file():
            if required:
                raise RuntimeError(f"FM3 goldens missing: {path} — run "
                                   f"`o55_fm3_lane.py goldens` first")
            return "skipped (no goldens file)"
        g = json.loads(Path(path).read_text(encoding='utf-8'))
        cols = list(g.get('cols') or [])
        for o in self.OUTS:
            if o not in cols:
                raise RuntimeError(f"{path} has no {o} column — regenerate it")
        idx = [cols.index(o) for o in self.OUTS]
        bad = []
        for i, (p, want) in enumerate(zip(g['probes'], g['outputs'])):
            r = self.run(p['headline'], p['body_200'])
            for o, j in zip(self.OUTS, idx):
                if r[o] != want[j]:
                    bad.append(f"probe {i} {o}: {r[o]} != {want[j]}")
        if bad:
            raise RuntimeError(f"FM3 prio goldens self-check FAILED ({len(bad)} mismatch(es)): "
                               + ' | '.join(bad[:6]))
        return f"ok ({len(g['probes'])} probes x {len(self.OUTS)})"


# ─── Parent side: o54's process-engine machinery, one FM3 worker ──────────────

class _Fm3WorkerProc(o54._WorkerProc):
    """o54's worker plumbing (call / handshake / kill / stop), pointed at THIS file."""

    def __init__(self, cpus, nice, goldens_required, logger):
        super().__init__(0, cpus, None, False, goldens_required, logger)
        self.nice = int(nice)

    def launch(self):
        """= o54._WorkerProc.launch with this file's worker command. ⚠ Same PDEATHSIG rule:
        call it from a thread that outlives the worker (main, or the lane's feeder)."""
        from multiprocessing.connection import Connection
        parent_sock, child_sock = socket.socketpair()
        child_fd = child_sock.fileno()
        env = dict(os.environ)
        env.update(OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1',
                   TOKENIZERS_PARALLELISM='false')
        cmd = [sys.executable, '-u', str(Path(__file__).resolve()), '--worker',
               '--fd', str(child_fd), '--ppid', str(os.getpid()),
               '--cpus', ','.join(str(c) for c in self.cpus), '--nice', str(self.nice)]
        if not self.goldens_required:
            cmd.append('--no-goldens')
        self.proc = subprocess.Popen(cmd, pass_fds=(child_fd,), stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                     env=env, close_fds=True)
        child_sock.close()
        self.conn = Connection(parent_sock.detach())
        threading.Thread(target=self._relay_stderr, args=(self.proc,),
                         name="fm3-stderr", daemon=True).start()
        return self

    def _relay_stderr(self, proc):
        try:
            for line in iter(proc.stderr.readline, b''):
                self.log.info("[FM3w] %s", line.decode('utf-8', 'replace').rstrip())
        except Exception:
            pass


class Fm3Lane(o54.ProcessEngine):
    """ONE FM3 worker behind o54.ProcessEngine's queue/feeder/timeout/respawn.

    launch()      start the worker subprocess and return at once (it loads for ~10 s in
                  parallel with whatever the caller does next)
    wait_ready()  collect its handshake (models loaded, goldens checked), start the feeder
    submit_pr(headline, body_200) -> Future of {FM3_COLS..., '_ms', '_done_epoch'}

    THE QUIET GATE (`quiet`, a callable -> bool, from the orchestrator): a job goes to the
    worker only while quiet() is True — no PR entered scoring in the last FM3_QUIET_MS and
    the engine queue is empty — or after FM3_MAX_HOLD_S. Measured 2026-09-27 without it:
    FM3 work of a burst's first PRs ran while the later PRs were scored, and burst max went
    168 -> 254 ms (nice 19 cannot stop it: the worker lands on the idle hyperthread
    SIBLING of an engine thread and the two share one core)."""
    kind = 'fm3'

    def __init__(self, cpus=o54.DEFAULT_INFER_CPUS, nice=FM3_NICE, goldens_required=True,
                 logger=None, task_timeout=FM3_TASK_TIMEOUT_S, quiet=None,
                 max_hold_s=FM3_MAX_HOLD_S):
        super().__init__(workers=1, intra_op=1, cpus=cpus, spinning=False,
                         goldens_required=goldens_required, logger=logger,
                         task_timeout=task_timeout)
        self.nice = int(nice)
        self.t_launch = None
        self.ready_s = None
        self.quiet = quiet
        self.max_hold_s = float(max_hold_s)
        self.n_held = 0            # jobs the gate delayed
        self.n_forced = 0          # jobs sent after max_hold_s without a quiet moment

    def _hold_until_quiet(self):
        if self.quiet is None:
            return
        t0 = time.monotonic()
        held = False
        while not self._stopping:
            try:
                if self.quiet():
                    break
            except Exception:                                 # noqa: BLE001
                break                           # a broken predicate must not stall FM3
            held = True
            if time.monotonic() - t0 >= self.max_hold_s:
                self.n_forced += 1
                break
            time.sleep(0.01)
        self.n_held += held

    def _feed(self, w):
        """= o54.ProcessEngine._feed + the quiet gate before each job."""
        while True:
            _p, _s, f, kind, args = self._q.get()
            if f is None:
                return
            if kind == 'fm3':
                self._hold_until_quiet()
            if not f.set_running_or_notify_cancel():
                continue
            try:
                f.set_result(w.call(kind, args, self.task_timeout))
            except o54.WorkerDied as exc:
                f.set_exception(exc)
                self.log.error("[FM3] %s — respawning the FM3 worker", exc)
                self._respawn(w)
            except BaseException as exc:                      # noqa: BLE001
                f.set_exception(exc)

    def launch(self):
        self._procs = [_Fm3WorkerProc(self.cpu_sets[0], self.nice, self.goldens_required,
                                      self.log)]
        self.t_launch = time.monotonic()
        self._procs[0].launch()
        return self

    def wait_ready(self, timeout: float = o54.WORKER_READY_TIMEOUT_S):
        w = self._procs[0]
        w.handshake(timeout)
        self.ready_s = round(time.monotonic() - self.t_launch, 1)
        self._feeders = [threading.Thread(target=self._feed, args=(w,), name="fm3-feeder",
                                          daemon=True)]
        self._feeders[0].start()
        return self

    def start(self):
        return self.launch().wait_ready()

    def submit_pr(self, headline: str, body_200: str):
        return self.submit(PRIO_FM3, 'fm3', headline, body_200)

    def describe(self) -> str:
        i = self._procs[0].info if self._procs else {}
        return (f"fm3 lane pid={i.get('pid')} CPUs={i.get('cpus')} nice={i.get('nice')} "
                f"load={i.get('load_s')}s ready_after={self.ready_s}s "
                f"goldens={i.get('goldens')} quiet_gate={'on' if self.quiet else 'off'}")


class _Fm3PrioWorkerProc(_Fm3WorkerProc):
    """The same worker plumbing, started with --prio (Fm3PrioModels, task kind 'fm3p')."""

    def launch(self):
        # ⚠ same PDEATHSIG rule as _Fm3WorkerProc.launch: call from a long-lived thread.
        from multiprocessing.connection import Connection
        parent_sock, child_sock = socket.socketpair()
        child_fd = child_sock.fileno()
        env = dict(os.environ)
        env.update(OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1',
                   TOKENIZERS_PARALLELISM='false')
        cmd = [sys.executable, '-u', str(Path(__file__).resolve()), '--worker', '--prio',
               '--fd', str(child_fd), '--ppid', str(os.getpid()),
               '--cpus', ','.join(str(c) for c in self.cpus), '--nice', str(self.nice)]
        if not self.goldens_required:
            cmd.append('--no-goldens')
        self.proc = subprocess.Popen(cmd, pass_fds=(child_fd,), stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                     env=env, close_fds=True)
        child_sock.close()
        self.conn = Connection(parent_sock.detach())
        threading.Thread(target=self._relay_stderr, args=(self.proc,),
                         name="fm3p-stderr", daemon=True).start()
        return self

    def _relay_stderr(self, proc):
        try:
            for line in iter(proc.stderr.readline, b''):
                self.log.info("[FM3p] %s", line.decode('utf-8', 'replace').rstrip())
        except Exception:
            pass


class Fm3PrioLane(Fm3Lane):
    """The priority FM3 worker: ONLY FM3_bd_ge50_FT + FM3_hd_reg_FT, for the trade decision. Same machinery
    as Fm3Lane but no quiet gate (a decision is waiting on it), nice 0, short timeout.
    submit_bd(headline, body_200, prio) -> Future of {FM3P_COLS..., '_ms', '_done_epoch'}"""
    kind = 'fm3p'

    def __init__(self, cpus=o54.DEFAULT_INFER_CPUS, nice=FM3P_NICE, goldens_required=True,
                 logger=None, task_timeout=FM3P_TASK_TIMEOUT_S):
        super().__init__(cpus=cpus, nice=nice, goldens_required=goldens_required,
                         logger=logger, task_timeout=task_timeout, quiet=None)

    def _feed(self, w):
        """= o54.ProcessEngine._feed: no quiet gate, nothing is held."""
        return o54.ProcessEngine._feed(self, w)

    def launch(self):
        self._procs = [_Fm3PrioWorkerProc(self.cpu_sets[0], self.nice, self.goldens_required,
                                          self.log)]
        self.t_launch = time.monotonic()
        self._procs[0].launch()
        return self

    def submit_bd(self, headline: str, body_200: str, prio: int = o54.PRIO_TRADEABLE):
        return self.submit(prio, 'fm3p', headline, body_200)

    def describe(self) -> str:
        i = self._procs[0].info if self._procs else {}
        return (f"fm3 prio lane pid={i.get('pid')} CPUs={i.get('cpus')} nice={i.get('nice')} "
                f"load={i.get('load_s')}s ready_after={self.ready_s}s "
                f"goldens={i.get('goldens')} quiet_gate=off")


# ─── Worker subprocess entry point ───────────────────────────────────────────

def _worker_main(argv) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--worker', action='store_true')
    ap.add_argument('--fd', type=int, required=True)
    ap.add_argument('--ppid', type=int, required=True)
    ap.add_argument('--cpus', required=True)
    ap.add_argument('--nice', type=int, default=FM3_NICE)
    ap.add_argument('--no-goldens', action='store_true')
    ap.add_argument('--prio', action='store_true',
                    help="the priority worker: Fm3PrioModels, task kind 'fm3p'")
    a = ap.parse_args(argv)

    o54._set_pdeathsig()
    if os.getppid() != a.ppid:                  # parent died before prctl took effect
        return 0
    signal.signal(signal.SIGINT, signal.SIG_IGN)   # the parent stops it (or PDEATHSIG)
    os.sched_setaffinity(0, set(o54.parse_cpus(a.cpus)))
    try:
        os.nice(a.nice)
    except OSError:
        pass

    logging.basicConfig(stream=sys.stderr, level=logging.WARNING,
                        format='%(levelname)s %(name)s: %(message)s')
    wlog = logging.getLogger('o55.fm3')
    wlog.setLevel(logging.INFO)

    from multiprocessing.connection import Connection
    conn = Connection(a.fd)
    try:
        m = (Fm3PrioModels() if a.prio else Fm3Models()).load()
        gold = m.self_check(required=not a.no_goldens)
        info = {'pid': os.getpid(), 'cpus': sorted(os.sched_getaffinity(0)),
                'nice': os.nice(0), 'load_s': m.load_s, 'goldens': gold}
        wlog.info("FM3%s worker ready: pid %d CPUs %s nice %d load %.1fs goldens %s",
                  'p' if a.prio else '', info['pid'], info['cpus'], info['nice'], m.load_s, gold)
        conn.send(('ready', info))
    except BaseException:
        try:
            conn.send(('error', traceback.format_exc()))
        except Exception:
            pass
        return 1

    while True:
        try:
            msg = conn.recv()
        except (EOFError, OSError):
            return 0
        if not msg or msg[0] == 'stop':
            return 0
        _tag, tid, kind, args = msg
        try:
            if kind == ('fm3p' if a.prio else 'fm3'):
                res = m.run(*args)
            elif kind == 'ping':
                res = {'pid': os.getpid(), 'cpus': sorted(os.sched_getaffinity(0))}
            else:
                raise ValueError(f"unknown task kind {kind!r}")
        except Exception as exc:                  # noqa: BLE001
            conn.send(('exc', tid, f"{type(exc).__name__}: {exc}"))
            continue
        conn.send(('ok', tid, res))


# ─── goldens / check ──────────────────────────────────────────────────────────

def tape_rows(n: int, tables: Path = TABLES_DIR) -> list:
    """(Headline, Body cell) of the newest live-TSV rows, distinct headlines, non-empty.
    ⚠ news_output_*.tsv is csv.DictWriter output (QUOTE_MINIMAL): default csv quoting."""
    csv.field_size_limit(sys.maxsize)
    seen, out = set(), []
    for p in reversed(sorted(glob.glob(str(Path(tables) / "news_output_????-??-??.tsv")))):
        with open(p, newline='', encoding='utf-8') as fh:
            rows = list(csv.DictReader(fh, delimiter='\t'))
        for r in reversed(rows):
            h = (r.get('Headline') or '').strip()
            if h and h not in seen:
                seen.add(h)
                out.append((r.get('Headline') or '', r.get('Body') or ''))
            if len(out) >= n:
                return out
    return out


def cmd_goldens(a) -> int:
    m = Fm3Models().load()
    rows = tape_rows(8)
    probes = [{'headline': h, 'body_200': m.body_200(h, b)} for h, b in rows]
    probes += [{'headline': "Acme Corp Announces <b>Record</b> Revenue &amp; Guidance",
                'body_200': ""},                                     # HTML headline, no body
               {'headline': "", 'body_200': m.body_200("", "Acme Corp today announced a "
                                                           "$25 million contract award.")}]
    out = m.compute_goldens(probes)
    GOLDENS_FILE.parent.mkdir(parents=True, exist_ok=True)
    GOLDENS_FILE.write_text(json.dumps(
        {'created': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
         'twins': {t: str(d) for t, d in FM3_TWINS}, 'cols': list(FM3_COLS),
         'probes': probes, 'outputs': out}, indent=1), encoding='utf-8')
    print(f"[FM3] goldens: {len(probes)} probes x {len(FM3_COLS)} outputs -> {GOLDENS_FILE}")
    print(f"[FM3] self-check against the file just written: {m.self_check()}")
    return 0


def _vm_rss_mb(pid) -> float:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith('VmRSS:'):
                return round(int(line.split()[1]) / 1024.0, 0)
    except Exception:
        pass
    return float('nan')


def cmd_check(n: int = 200, cpus=None, nice: int = FM3_NICE, logger=None) -> int:
    """The lane as production starts it (subprocess, pinned, niced, goldens), then n
    real tape PRs one at a time: wall time submit->result and the worker's compute time."""
    import numpy as np
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    t0 = time.monotonic()
    lane = Fm3Lane(cpus=o54.parse_cpus(cpus), nice=nice, logger=logger or log).start()
    print(f"[FM3] {lane.describe()} (start {time.monotonic() - t0:.1f}s)")
    body_mod = o54.load_by_path("o55_check_body_prep",
                                SCRIPTS_DIR / "tools" / "Encoder_Models" / "body_prep.py")
    rows = [(h, body_mod.prep_body(b, h, 200)) for h, b in tape_rows(n)]
    wall, comp, bad = [], [], []
    try:
        for h, b in rows:
            t = time.perf_counter()
            r = lane.submit_pr(h, b).result(timeout=FM3_TASK_TIMEOUT_S + 5)
            wall.append((time.perf_counter() - t) * 1000.0)
            comp.append(r['_ms'])
            if not all(isinstance(r.get(c), float) and math.isfinite(r[c]) for c in FM3_COLS):
                bad.append(h[:60])
        pid = lane._procs[0].info.get('pid')
        rss = _vm_rss_mb(pid)
    finally:
        lane.shutdown()
    p = lambda v: np.percentile(v, [50, 95, 99])               # noqa: E731
    w, c = p(wall), p(comp)
    print(f"[FM3] {len(rows)} tape PRs, both twins, 1 thread, nice {nice}: "
          f"worker compute p50/p95/p99 {c[0]:.1f}/{c[1]:.1f}/{c[2]:.1f} ms | "
          f"submit->result {w[0]:.1f}/{w[1]:.1f}/{w[2]:.1f} ms | worker RSS {rss:.0f} MB")
    if bad:
        print(f"[FM3] FAILED: {len(bad)} row(s) with a blank/non-finite output: {bad[:5]}")
        return 1
    print("[FM3] OK")
    return 0


def cmd_check_prio(n: int = 200, cpus=None, nice: int = FM3P_NICE, logger=None) -> int:
    """The priority lane as production starts it, then n tape PRs one at a time: timings,
    and PARITY -- every value must equal Fm3Models' FM3P_COLS (loaded in-process)."""
    import numpy as np
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    t0 = time.monotonic()
    lane = Fm3PrioLane(cpus=o54.parse_cpus(cpus), nice=nice, logger=logger or log).start()
    print(f"[FM3p] {lane.describe()} (start {time.monotonic() - t0:.1f}s)")
    body_mod = o54.load_by_path("o55_check_body_prep",
                                SCRIPTS_DIR / "tools" / "Encoder_Models" / "body_prep.py")
    rows = [(h, body_mod.prep_body(b, h, 200)) for h, b in tape_rows(n)]
    wall, comp, got, bad = [], [], [], []
    try:
        for h, b in rows:
            t = time.perf_counter()
            r = lane.submit_bd(h, b).result(timeout=FM3P_TASK_TIMEOUT_S + 5)
            wall.append((time.perf_counter() - t) * 1000.0)
            comp.append(r['_ms'])
            v = tuple(r.get(o) for o in FM3P_COLS)
            got.append(v)
            if not all(isinstance(x, float) and math.isfinite(x) for x in v):
                bad.append(f"blank/non-finite: {h[:60]}")
        pid = lane._procs[0].info.get('pid')
        rss = _vm_rss_mb(pid)
    finally:
        lane.shutdown()
    # parity against the shadow lane's own scorer, both twins, in this process
    full = Fm3Models().load()
    for (h, b), v in zip(rows, got):
        r = full.run(h, b)
        want = tuple(r[o] for o in FM3P_COLS)
        if v != want:
            bad.append(f"parity: {h[:50]!r} prio {v} != shadow {want}")
    p = lambda v: np.percentile(v, [50, 95, 99])               # noqa: E731
    w, c = p(wall), p(comp)
    print(f"[FM3p] {len(rows)} tape PRs, FM3_bd_ge50_FT + FM3_hd_reg_FT in one task, 1 thread, nice {nice}: "
          f"worker compute p50/p95/p99 {c[0]:.1f}/{c[1]:.1f}/{c[2]:.1f} ms | "
          f"submit->result {w[0]:.1f}/{w[1]:.1f}/{w[2]:.1f} ms | worker RSS {rss:.0f} MB")
    print(f"[FM3p] parity vs Fm3Models {FM3P_COLS[0]} + {FM3P_COLS[1]}: "
          f"{sum(1 for x in bad if x.startswith('parity'))} mismatch(es) of {len(rows)}")
    if bad:
        print(f"[FM3p] FAILED: {len(bad)} problem(s): {bad[:5]}")
        return 1
    print("[FM3p] OK")
    return 0


def main():
    if '--worker' in sys.argv:
        sys.exit(_worker_main(sys.argv[1:]))
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('goldens')
    c = sub.add_parser('check')
    c.add_argument('--n', type=int, default=200)
    c.add_argument('--cpus', default=None, help="default: the orchestrator's 1-3,5-7")
    c.add_argument('--nice', type=int, default=FM3_NICE)
    c = sub.add_parser('check-prio', help="the priority FM3_bd_ge50_FT + FM3_hd_reg_FT worker + parity")
    c.add_argument('--n', type=int, default=200)
    c.add_argument('--cpus', default=None, help="default: the orchestrator's 1-3,5-7")
    c.add_argument('--nice', type=int, default=FM3P_NICE)
    a = ap.parse_args()
    if a.cmd == 'goldens':
        sys.exit(cmd_goldens(a))
    if a.cmd == 'check-prio':
        sys.exit(cmd_check_prio(a.n, a.cpus, a.nice))
    sys.exit(cmd_check(a.n, a.cpus, a.nice))


if __name__ == '__main__':
    main()
