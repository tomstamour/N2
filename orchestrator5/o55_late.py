"""o55_late.py — the two generic pieces of the orchestrator's "late stage".

Orchestrator5.5 writes each TSV row right after the trade decision with some cells blank,
and fills them ~5 minutes later, so slow non-trading models never run during a PR burst.
This module knows nothing about models or the orchestrator; it holds only:

  LateScheduler   runs jobs a fixed delay after they were submitted, one at a time, and
                  only when the box is quiet (or after a hard deadline).
  fill_tsv_rows   rewrites a daily TSV, changing only the named cells of the named rows,
                  verified cell for cell and swapped in with an atomic os.replace.

⚠ fill_tsv_rows must be called with the orchestrator's own `_tsv_lock` — the very lock
`_append_to_tsv` holds while it appends a row. The fill reads the whole file and replaces
it; a row appended in between would be silently lost. The lock makes that impossible.

Self-test:  /home/tom/venv/bin/python o55_late.py selftest
"""
import csv
import heapq
import itertools
import logging
import os
import sys
import threading
import time


# ─── LateScheduler ────────────────────────────────────────────────────────────

class LateScheduler:
    """Runs `run_job(job)` for each submitted job, `delay_s` after its t0, one job at a time,
    in due-time order, on one daemon thread.

    A due job waits while `quiet()` is False (the caller's "nothing is being scored right
    now" test, polled every `quiet_poll_s`). It is forced to run anyway once
    `t0 + max_wait_s` has passed, so a permanently busy box cannot starve the late stage.
    `run_job` may raise: the scheduler logs the traceback and goes on to the next job.

    Never busy-spins: with nothing due it sleeps until the next due time or a submit/flush/
    stop wakes it; it polls only while the head job is due and held back by quiet()."""

    def __init__(self, run_job, delay_s: float = 300.0, quiet=None,
                 quiet_poll_s: float = 0.05, max_wait_s: float = 600.0, logger=None):
        self._run_job = run_job
        self.delay_s = float(delay_s)
        self._quiet = quiet
        self._quiet_poll_s = float(quiet_poll_s)
        self.max_wait_s = float(max_wait_s)
        self._log = logger or logging.getLogger("o55_late")
        self._cv = threading.Condition()
        self._heap = []                 # (due_epoch, counter, t0_epoch, job)
        self._counter = itertools.count()   # tie-break: equal due times run in submit order
        self._running = False           # a job is executing right now
        self._flushing = 0              # >0 while a flush() is waiting: ignore due time + quiet
        self._stop = False
        self._quiet_err_logged = False
        self.n_forced = 0               # jobs run because max_wait_s passed, not because quiet
        self.n_done = 0
        self.n_failed = 0
        self._thread = threading.Thread(target=self._loop, name="late-scheduler", daemon=True)
        self._thread.start()

    # -- public ---------------------------------------------------------------

    def submit(self, job, t0_epoch: float) -> None:
        """`job` becomes due at t0_epoch + delay_s."""
        with self._cv:
            heapq.heappush(self._heap, (t0_epoch + self.delay_s, next(self._counter),
                                        t0_epoch, job))
            self._cv.notify_all()

    def flush(self, timeout_s: float) -> int:
        """Make every pending job due NOW, ignore quiet(), run them all. Returns the count
        still pending (queued + running) when done or when `timeout_s` runs out."""
        deadline = time.monotonic() + timeout_s
        with self._cv:
            self._flushing += 1
            self._cv.notify_all()
            try:
                while (self._heap or self._running) and not self._stop:
                    left = deadline - time.monotonic()
                    if left <= 0:
                        break
                    self._cv.wait(left)
                return len(self._heap) + (1 if self._running else 0)
            finally:
                self._flushing -= 1

    def stop(self, join_timeout_s: float = 2.0) -> None:
        """Stop the thread after the current job. Does NOT flush: queued jobs are dropped."""
        with self._cv:
            self._stop = True
            self._cv.notify_all()
        if threading.current_thread() is not self._thread:
            self._thread.join(join_timeout_s)

    def pending(self) -> int:
        with self._cv:
            return len(self._heap) + (1 if self._running else 0)

    def due_count(self) -> int:
        """Queued jobs already due (or all of them during a flush) — not the running one.
        A job can call this to batch its output: 0 means nothing else is about to run."""
        with self._cv:
            if self._flushing:
                return len(self._heap)
            now = time.time()
            return sum(1 for due, _c, _t0, _j in self._heap if due <= now)

    def describe(self) -> str:
        with self._cv:
            nxt = f"{max(0.0, self._heap[0][0] - time.time()):.0f}s" if self._heap else "-"
            return (f"late: pending={len(self._heap)} running={int(self._running)} "
                    f"done={self.n_done} failed={self.n_failed} forced={self.n_forced} "
                    f"next_due_in={nxt} delay={self.delay_s:.0f}s")

    # -- worker ---------------------------------------------------------------

    def _is_quiet(self) -> bool:
        """quiet() is the caller's code: a crash in it must not strand the late stage, so it
        counts as quiet (logged once)."""
        if self._quiet is None:
            return True
        try:
            return bool(self._quiet())
        except Exception:                                   # noqa: BLE001
            if not self._quiet_err_logged:
                self._quiet_err_logged = True
                self._log.error("[Late] quiet() raised — treating as quiet", exc_info=True)
            return True

    def _loop(self) -> None:
        while True:
            with self._cv:
                job = None
                while not self._stop:
                    if not self._heap:
                        self._cv.wait()                     # nothing queued: sleep till submit
                        continue
                    due, _, t0, head = self._heap[0]
                    now = time.time()
                    if self._flushing:
                        job = heapq.heappop(self._heap)[3]
                        break
                    if now < due:
                        self._cv.wait(due - now)            # sleep to the due time (or a wake)
                        continue
                    # Due. quiet() runs outside the lock: it may take its own locks.
                    self._cv.release()
                    try:
                        ok = self._is_quiet()
                    finally:
                        self._cv.acquire()
                    if self._stop:
                        break
                    if not self._heap or self._heap[0][3] is not head:
                        continue                            # changed while unlocked: re-look
                    if self._flushing:
                        continue
                    forced = (not ok) and time.time() >= t0 + self.max_wait_s
                    if ok or forced:
                        heapq.heappop(self._heap)
                        if forced:
                            self.n_forced += 1
                        job = head
                        break
                    self._cv.wait(self._quiet_poll_s)       # held back by quiet(): poll
                if self._stop:
                    return
                self._running = True
            try:
                self._run_job(job)
                ok_run = True
            except Exception:                               # noqa: BLE001
                ok_run = False
                self._log.error("[Late] job failed", exc_info=True)
            finally:
                with self._cv:
                    self._running = False
                    if ok_run:
                        self.n_done += 1
                    else:
                        self.n_failed += 1
                    self._cv.notify_all()                   # wakes a waiting flush()


# ─── fill_tsv_rows ────────────────────────────────────────────────────────────

def fill_tsv_rows(path: str, updates: dict, key_cols: tuple, columns: list, lock,
                  logger=None) -> dict:
    """Fill cells of existing rows of the daily TSV `path`, in place.

    updates   {key_tuple: {col: value}}; key_tuple holds strings equal to the row's
              `key_cols` cells as written. Only the named cells change (None -> ''); every
              other cell of every row stays byte-for-byte what it was.
    columns   the header the file must have (the orchestrator's _TSV_COLUMNS). A different
              header means the file is not ours to touch: nothing is written.
    lock      the orchestrator's `_tsv_lock` — the one `_append_to_tsv` holds. The whole
              read-modify-replace runs under it, so no appended row can be lost.

    Returns {'status', 'updated', 'missing', 'dup', 'ms'}. status is 'ok', 'no_file',
    'header_mismatch', 'bad_column', 'malformed_rows' or 'verify_failed' (nothing written
    for any status but 'ok'). Never raises for a missing file; an unexpected I/O error is
    returned as status 'error:<exc>'."""
    log = logger or logging.getLogger("o55_late")
    t_start = time.monotonic()
    res = {'status': 'ok', 'updated': 0, 'missing': [], 'dup': [], 'ms': 0.0}

    def _done(status):
        res['status'] = status
        res['ms'] = round((time.monotonic() - t_start) * 1000.0, 1)
        return res

    d, name = os.path.split(path)
    tmp = os.path.join(d, f".{name}.late.tmp")      # hidden: no news_output_* glob matches it
    csv.field_size_limit(sys.maxsize)               # Body cells; the file is DictWriter output
    try:
        with lock:
            if not os.path.isfile(path):
                return _done('no_file')
            with open(path, 'r', newline='', encoding='utf-8') as f:
                rd = csv.DictReader(f, delimiter='\t')
                header = list(rd.fieldnames or [])
                rows = list(rd)
            if header != list(columns):
                return _done('header_mismatch')
            colset = set(columns)
            for key in key_cols:
                if key not in colset:
                    return _done('bad_column')
            for upd in updates.values():
                if any(c not in colset for c in upd):
                    return _done('bad_column')
            # A row with more or fewer cells than the header is not ours: DictReader would
            # hide it (key None / value None) and the rewrite would change it.
            if any(None in r or any(v is None for v in r.values()) for r in rows):
                return _done('malformed_rows')

            index = {}                              # key tuple -> [row indexes]
            for i, r in enumerate(rows):
                index.setdefault(tuple(r[k] for k in key_cols), []).append(i)
            touched = {}                            # row index -> {col: new value}
            for key, upd in updates.items():
                hits = index.get(tuple(key), [])
                if not hits:
                    res['missing'].append(key)
                    continue
                if len(hits) > 1:
                    res['dup'].append(key)
                for i in hits:
                    touched.setdefault(i, {}).update(
                        {c: ('' if v is None else str(v)) for c, v in upd.items()})
            if not touched:
                return _done('ok')                  # nothing to write; the file stays as is

            originals = [dict(r) for r in rows]
            for i, upd in touched.items():
                rows[i].update(upd)
            try:
                with open(tmp, 'w', newline='', encoding='utf-8') as f:
                    # Same writer settings as _append_to_tsv: default QUOTE_MINIMAL and
                    # the default '\r\n' terminator (the file is opened with newline='').
                    w = csv.DictWriter(f, fieldnames=list(columns), delimiter='\t',
                                       extrasaction='raise')
                    w.writeheader()
                    w.writerows(rows)
                    f.flush()
                    os.fsync(f.fileno())
                with open(tmp, 'r', newline='', encoding='utf-8') as f:
                    rd2 = csv.DictReader(f, delimiter='\t')
                    head2 = list(rd2.fieldnames or [])
                    again = list(rd2)
                bad = None
                if head2 != list(columns) or len(again) != len(originals):
                    bad = "header or row count differs"
                else:
                    for i, (a, o) in enumerate(zip(again, originals)):
                        upd = touched.get(i, {})
                        for c in columns:
                            want = upd[c] if c in upd else o[c]
                            if a.get(c) != want:
                                bad = f"row {i} column {c} differs"
                                break
                        if bad:
                            break
                if bad:
                    log.error(f"[Late] TSV fill verify FAILED ({bad}) — {path} left untouched")
                    return _done('verify_failed')
                os.replace(tmp, path)
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
            res['updated'] = len(touched)
            return _done('ok')
    except Exception as exc:                        # noqa: BLE001
        log.error(f"[Late] TSV fill error: {exc}", exc_info=True)
        return _done(f"error:{exc}")


# ─── self-test ────────────────────────────────────────────────────────────────

def _selftest() -> int:
    import tempfile
    import shutil
    fails = []

    def check(label, cond, extra=""):
        print(f"{'PASS' if cond else 'FAIL'}  {label}{(' — ' + str(extra)) if extra else ''}")
        if not cond:
            fails.append(label)

    base = "/tmp/claude-1000"
    os.makedirs(base, exist_ok=True)
    tdir = tempfile.mkdtemp(prefix="o55_late_", dir=base)
    try:
        # ---- fill_tsv_rows ----
        cols = ['ID', 'Symbol', 'Body', 'Score', 'Late1', 'Late2']
        path = os.path.join(tdir, "news_output_2026-01-02.tsv")
        nasty = 'He said "yes"\tthen\nleft,\r\nwith a "quoted\ttab" and ; stuff'
        data = [
            {'ID': '1', 'Symbol': 'AAA', 'Body': nasty, 'Score': '0.5', 'Late1': '', 'Late2': ''},
            {'ID': '1', 'Symbol': 'BBB', 'Body': 'plain', 'Score': '0.6', 'Late1': 'x', 'Late2': ''},
            {'ID': '2', 'Symbol': 'AAA', 'Body': '"', 'Score': '', 'Late1': '', 'Late2': ''},
            {'ID': '3', 'Symbol': 'CCC', 'Body': 'a\tb', 'Score': '0.1', 'Late1': '', 'Late2': ''},
            {'ID': '3', 'Symbol': 'CCC', 'Body': 'dup twin', 'Score': '0.2', 'Late1': '', 'Late2': ''},
        ]
        with open(path, 'w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=cols, delimiter='\t', extrasaction='ignore')
            w.writeheader()
            w.writerows(data)
        lock = threading.Lock()
        upd = {('1', 'AAA'): {'Late1': 0.25, 'Late2': 'ok'},
               ('2', 'AAA'): {'Late1': None, 'Late2': 'fine'},
               ('3', 'CCC'): {'Late2': 'both'},
               ('9', 'ZZZ'): {'Late1': 'ghost'}}
        csv.field_size_limit(sys.maxsize)
        r = fill_tsv_rows(path, upd, ('ID', 'Symbol'), cols, lock)
        check("fill status ok", r['status'] == 'ok', r)
        check("fill updated=4 rows (3 keys, one hits 2 rows)", r['updated'] == 4, r['updated'])
        check("fill missing reported", r['missing'] == [('9', 'ZZZ')], r['missing'])
        check("fill dup reported", r['dup'] == [('3', 'CCC')], r['dup'])
        with open(path, 'r', newline='', encoding='utf-8') as f:
            got = list(csv.DictReader(f, delimiter='\t'))
        expect = [dict(x) for x in data]
        expect[0].update(Late1='0.25', Late2='ok')
        expect[2].update(Late1='', Late2='fine')
        expect[3].update(Late2='both')
        expect[4].update(Late2='both')
        check("fill: all cells as expected after re-read (Body with quotes/tabs/newlines intact)",
              got == expect)
        check("fill: Body cells byte-identical",
              [g['Body'] for g in got] == [x['Body'] for x in data])
        check("fill: temp file removed",
              [n for n in os.listdir(tdir) if n.startswith('.')] == [])
        before = open(path, 'rb').read()
        r = fill_tsv_rows(path, {('1', 'AAA'): {'Nope': 1}}, ('ID', 'Symbol'), cols, lock)
        check("fill: unknown column refused, file untouched",
              r['status'] == 'bad_column' and open(path, 'rb').read() == before, r['status'])
        r = fill_tsv_rows(path, upd, ('ID', 'Symbol'), cols + ['Extra'], lock)
        check("fill: header mismatch -> no write",
              r['status'] == 'header_mismatch' and open(path, 'rb').read() == before, r['status'])
        r = fill_tsv_rows(os.path.join(tdir, "absent.tsv"), upd, ('ID',), cols, lock)
        check("fill: missing file -> no_file, no raise", r['status'] == 'no_file', r['status'])

        # ---- LateScheduler ----
        ran = []
        t_begin = time.time()
        quiet_from = t_begin + 0.5
        sched = LateScheduler(lambda j: (ran.append((j, time.time() - t_begin)),
                                         (_ for _ in ()).throw(ValueError("boom"))
                                         if j == 'bad' else None),
                              delay_s=0.3, quiet=lambda: time.time() >= quiet_from,
                              quiet_poll_s=0.02, max_wait_s=600.0,
                              logger=logging.getLogger("selftest"))
        logging.getLogger("selftest").addHandler(logging.NullHandler())
        logging.getLogger("selftest").propagate = False
        sched.submit('B', t_begin + 0.05)       # due 0.35
        sched.submit('A', t_begin)              # due 0.30  (submitted later, due earlier)
        sched.submit('bad', t_begin + 0.10)     # due 0.40, raises
        sched.submit('C', t_begin + 0.10)       # due 0.40, tie -> after 'bad' (counter)
        time.sleep(0.4)
        check("sched: nothing ran while quiet() is False", ran == [], ran)
        time.sleep(0.5)
        names = [n for n, _ in ran]
        check("sched: due-time order with counter tie-break", names == ['A', 'B', 'bad', 'C'], names)
        check("sched: ran only after quiet (>= 0.5 s)", all(t >= 0.49 for _, t in ran),
              [round(t, 2) for _, t in ran])
        check("sched: a raising job is counted and does not stop the rest",
              sched.n_failed == 1 and sched.n_done == 3, sched.describe())
        check("sched: pending 0", sched.pending() == 0)
        sched.stop()

        # max_wait force: quiet never true, max_wait 0.4 s from t0
        ran2 = []
        s2 = LateScheduler(ran2.append, delay_s=0.1, quiet=lambda: False, quiet_poll_s=0.02,
                           max_wait_s=0.4)
        t0 = time.time()
        s2.submit('F', t0)
        time.sleep(0.25)
        check("force: held while quiet() False before max_wait", ran2 == [])
        time.sleep(0.4)
        check("force: ran after max_wait, counted in n_forced",
              ran2 == ['F'] and s2.n_forced == 1, (ran2, s2.n_forced))

        # flush: far-future jobs, quiet never true
        ran3 = []
        s3 = LateScheduler(lambda j: (time.sleep(0.05), ran3.append(j)), delay_s=300.0,
                           quiet=lambda: False, max_wait_s=900.0)
        t0 = time.time()
        for n in ('x', 'y', 'z'):
            s3.submit(n, t0)
        time.sleep(0.1)
        check("flush: nothing ran before flush", ran3 == [] and s3.pending() == 3)
        left = s3.flush(5.0)
        check("flush: all run, 0 pending, in order", left == 0 and ran3 == ['x', 'y', 'z'],
              (left, ran3))
        s3.submit('slow', time.time())
        s3._run_job = lambda j: time.sleep(1.0)
        left = s3.flush(0.2)
        check("flush: timeout returns the count still pending", left == 1, left)
        s3.stop()
        s2.stop()
        check("describe() is a string", isinstance(s3.describe(), str), s3.describe())
    finally:
        shutil.rmtree(tdir, ignore_errors=True)
    print("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}")
    return 1 if fails else 0


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'selftest':
        sys.exit(_selftest())
    print("usage: python o55_late.py selftest")
