#!/usr/bin/env python
"""
o55_kw15ft.py -- Orchestrator5.5's kw15_FT: the two ge15 keyword pipelines refit on EVERY
labelled row, scored in the SAME engine task as today's kw15, before the decision.

    /home/tom/venv/bin/python o55_kw15ft.py check [--n 200]   # goldens + _FT parity + timing

THE MODELS (tools/keyword/model_trained_on_2026-05-04-2026-09-24/, kw15_ft_train.py)
  The live kw15 recipe (keyword_finder.py --task classification --binarize-threshold 15)
  refit on the 5,668 labelled PRs of 2026-05-04 -> 09-24 with NO 20% test split (today's
  kw15 saw 2,117 rows of 05-04 -> 06-18). The trainer's `repro` mode rebuilds today's two
  pipelines bit for bit, so only the rows differ.
    kw15_proba_bd_FT   the body pipeline    (same input string as kw15_proba_bd)
    kw15_proba_hd_FT   the headline pipeline (same input string as kw15_proba_hd)
  ⚠ NO held-out score: _FT trained on every labelled row. Rules MAY name the two fields
    (Orchestrator5.5 _TRIGGER_FIELDS) -- pick their cuts from live data only.

FAIL-OPEN WHILE NO RULE READS IT
  Missing files, a load error, or ANY digit off the trainer's own reference_scores.json
  turns _FT off (state logged, cells blank); kw15 and trading carry on, and an error while
  scoring _FT never costs kw15 its two values. Orchestrator5.5 refuses to start only when a
  loaded rule reads an _FT field and _FT is not 'ok' -- the XGB leg's policy.

REUSE, NOT A COPY: o54_engine.ModelSet (loading, warm-up, goldens) and ThreadEngine. Only
ModelSet.load / _t_kw15 / self_check and ThreadEngine.start are overridden; o54_engine.py is
not modified, so Orchestrator5.4 (the rollback) runs byte-identical code.
⚠ Thread engine only: `--engine procs` workers run o54_engine.py's own ModelSet, so the _FT
  cells stay blank there (make_engine / engine_state say so).
"""

import argparse
import json
import logging
import math
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent            # orchestrator5/
SCRIPTS_DIR = HERE.parent                         # N2/scripts
sys.path.insert(0, str(HERE))
import o54_engine as o54                          # noqa: E402

log = logging.getLogger('o55_kw15ft')

KW15FT_DIR = SCRIPTS_DIR / "tools" / "keyword" / "model_trained_on_2026-05-04-2026-09-24"
KW15FT_BODY_PIPELINE = (KW15FT_DIR / "classify_fromPRhigh_ge15_FT"
                        / "tfidf_fitted_pipeline.joblib")
KW15FT_HEAD_PIPELINE = (KW15FT_DIR / "classify_fromPRhigh_ge15_headline_FT"
                        / "tfidf_fitted_pipeline.joblib")
# Written by kw15_ft_train.py from RAW Headline / Body cells through the live prep: the
# engine must reproduce every value exactly, which proves train/serve parity end to end.
KW15FT_REFERENCE = KW15FT_DIR / "reference_scores.json"
KW15FT_COLS = ('kw15_proba_bd_FT', 'kw15_proba_hd_FT')


def state_ok(state: str) -> bool:
    return str(state).startswith('ok')


class Kw15FtModelSet(o54.ModelSet):
    """o54_engine.ModelSet + the two _FT pipelines inside the kw15 task."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.kw_body_ft = self.kw_head_ft = None
        self.kw15ft_state = 'not_loaded'

    @property
    def kw15ft_on(self) -> bool:
        return self.kw_body_ft is not None and self.kw_head_ft is not None

    def load(self):
        # BEFORE super().load(): its warm() runs the kw15 task, which then warms _FT too.
        if 'kw15' in self.kinds:
            try:
                import joblib
                self.kw_body_ft = joblib.load(KW15FT_BODY_PIPELINE)
                self.kw_head_ft = joblib.load(KW15FT_HEAD_PIPELINE)
                self.kw15ft_state = 'loaded'
            except Exception as exc:                          # noqa: BLE001
                self._ft_off(f"load failed: {type(exc).__name__}: {exc}")
        return super().load()

    def _ft_off(self, reason: str) -> None:
        self.kw_body_ft = self.kw_head_ft = None
        self.kw15ft_state = f"off ({reason})"

    def _t_kw15(self, headline: str, body_cell: str) -> dict:
        """o54_engine.ModelSet._t_kw15, then the _FT pair on the SAME two prepped strings.
        The first two values are computed exactly as there (the o54 goldens check them);
        `_ms` stays their time, `_ms_ft` is the _FT pair's."""
        with self._kw_lock:
            t0 = time.perf_counter()
            body_txt = self._kw15_prep(body_cell, o54.KW15_BODY_MAX_WORDS)
            head_txt = self._kw15_prep(headline, None)
            body_p = self.kw_body.predict_proba([body_txt])[0][1]
            head_p = self.kw_head.predict_proba([head_txt])[0][1]
            t1 = time.perf_counter()
            body_ft = head_ft = ms_ft = ft_error = None
            if self.kw15ft_on:
                try:
                    body_ft = float(round(float(
                        self.kw_body_ft.predict_proba([body_txt])[0][1]), 6))
                    head_ft = float(round(float(
                        self.kw_head_ft.predict_proba([head_txt])[0][1]), 6))
                    ms_ft = round((time.perf_counter() - t1) * 1000.0, 1)
                except Exception as exc:                      # noqa: BLE001
                    body_ft = head_ft = None
                    ft_error = f"{type(exc).__name__}: {exc}"
        return {'kw15_proba_bd': float(round(float(body_p), 6)),
                'kw15_proba_hd': float(round(float(head_p), 6)),
                'kw15_proba_bd_FT': body_ft, 'kw15_proba_hd_FT': head_ft,
                '_ms': round((t1 - t0) * 1000.0, 1), '_ms_ft': ms_ft, '_ft_error': ft_error,
                '_done_epoch': time.time()}

    # ── the 2026-10-07 split: kw_ft before the decision, kw_rest in the late stage ──
    # The gate reads only the _FT pair, so the decision path runs `kw_ft` alone and plain
    # kw15 moves to `kw_rest`, about 5 min later. Both build the SAME two prepped strings
    # as _t_kw15 and call the same pipelines, so every value equals the `kw15` task's
    # (the goldens and the reference still run through `kw15`; cmd_check compares them).
    def run(self, kind: str, *args):
        if kind == 'kw_ft':
            return self._t_kw_ft(*args)
        if kind == 'kw_rest':
            return self._t_kw_rest(*args)
        return super().run(kind, *args)

    def _t_kw_ft(self, headline: str, body_cell: str) -> dict:
        """The _FT pair only -- _t_kw15's second half, on the same prep."""
        with self._kw_lock:
            t0 = time.perf_counter()
            body_ft = head_ft = ms_ft = ft_error = None
            if self.kw15ft_on:
                try:
                    body_txt = self._kw15_prep(body_cell, o54.KW15_BODY_MAX_WORDS)
                    head_txt = self._kw15_prep(headline, None)
                    body_ft = float(round(float(
                        self.kw_body_ft.predict_proba([body_txt])[0][1]), 6))
                    head_ft = float(round(float(
                        self.kw_head_ft.predict_proba([head_txt])[0][1]), 6))
                    ms_ft = round((time.perf_counter() - t0) * 1000.0, 1)
                except Exception as exc:                      # noqa: BLE001
                    body_ft = head_ft = None
                    ft_error = f"{type(exc).__name__}: {exc}"
        return {'kw15_proba_bd_FT': body_ft, 'kw15_proba_hd_FT': head_ft,
                '_ms_ft': ms_ft, '_ft_error': ft_error, '_done_epoch': time.time()}

    def _t_kw_rest(self, headline: str, body_cell: str) -> dict:
        """Plain kw15 only (o54_engine's own _t_kw15) -- the late-stage half."""
        return o54.ModelSet._t_kw15(self, headline, body_cell)

    def self_check(self, path: Path = o54.GOLDENS_FILE, required: bool = True) -> str:
        """o54's goldens first -- hard-fail exactly as before: today's kw15 digits must not
        move. Then _FT against the trainer's reference_scores.json -- fail-open."""
        base = super().self_check(path, required)
        if 'kw15' in self.kinds and self.kw15ft_on:
            self.kw15ft_state = self._check_reference()
        return f"{base}; kw15_FT {self.kw15ft_state}"

    def _check_reference(self) -> str:
        try:
            ref = json.loads(KW15FT_REFERENCE.read_text(encoding='utf-8'))
            bad = []
            for r in ref['rows']:
                got = self._t_kw15(r['Headline'], r['Body'])
                if got.get('_ft_error'):
                    raise RuntimeError(got['_ft_error'])
                bad += [f"{r['ID']}.{c}: {got.get(c)} != {r[c]}"
                        for c in KW15FT_COLS if got.get(c) != r[c]]
            if bad:
                raise ValueError(f"{len(bad)} value(s) off the trainer's reference: "
                                 + ' | '.join(bad[:4]))
            return f"ok ({len(ref['rows'])} reference rows exact)"
        except Exception as exc:                              # noqa: BLE001
            self._ft_off(f"{type(exc).__name__}: {exc}")
            return self.kw15ft_state


class Kw15FtThreadEngine(o54.ThreadEngine):
    """o54_engine.ThreadEngine with Kw15FtModelSet: start() is the base one verbatim except
    for the ModelSet class (same pools, same routing, same single kw15 thread)."""

    def start(self):
        self.models = Kw15FtModelSet(self.intra_op, self.spinning).load()
        self.goldens = self.models.self_check(required=self.goldens_required)
        run = self.models.run
        if self.pools == 'shared':
            main = o54._PriorityPool('eng', self.workers, run)
            kw = o54._PriorityPool('kw15', 1, run)
            self._route = {k: main for k in ('prosus', 'fmb', 'flang', 'embed', 'xgb')}
            self._route.update({k: kw for k in ('kw15', 'kw_ft', 'kw_rest')})
            self._pools = [main, kw]
        else:
            p = {k: o54._PriorityPool(k, self.workers, run)
                 for k in ('prosus', 'fmb', 'flang', 'xgb')}
            p['kw15'] = p['kw_ft'] = p['kw_rest'] = o54._PriorityPool('kw15', 1, run)
            p['embed'] = o54._PriorityPool('embed', 1, run)
            self._route = p
            self._pools = list({id(v): v for v in p.values()}.values())   # kw pool once
        return self


def make_engine(kind='threads', pools='shared', workers=6, intra_op=1, cpus=None,
                spinning=False, goldens_required=True, logger=None, kw15ft=True):
    """o54_engine.make_engine, with the kw15_FT ModelSet on the thread engine. kw15ft=False
    (--no-kw15ft) or kind='procs' returns the plain o54 engine: the _FT cells stay blank."""
    if kind == 'threads' and kw15ft:
        return Kw15FtThreadEngine(pools=pools, workers=workers, intra_op=intra_op,
                                  spinning=spinning, goldens_required=goldens_required)
    return o54.make_engine(kind=kind, pools=pools, workers=workers, intra_op=intra_op,
                           cpus=cpus, spinning=spinning, goldens_required=goldens_required,
                           logger=logger)


def engine_state(engine, enabled: bool = True) -> str:
    """'ok (...)' | 'off (...)' | 'disabled (--no-kw15ft)' | 'unsupported (...)'."""
    if not enabled:
        return 'disabled (--no-kw15ft)'
    if isinstance(engine, Kw15FtThreadEngine):
        return engine.models.kw15ft_state if engine.models else 'not_loaded'
    return "unsupported (procs engine: its workers run o54_engine's own ModelSet)"


def disable(engine, reason: str) -> str:
    """Turn _FT off in a running engine (fail-open) and return the new state."""
    models = getattr(engine, 'models', None)
    if isinstance(models, Kw15FtModelSet):
        models._ft_off(reason)
        return models.kw15ft_state
    return engine_state(engine)


# ─── check ────────────────────────────────────────────────────────────────────

def cmd_check(n: int = 200) -> int:
    """kw15 alone, as the engine runs it (kinds=('kw15',), this process): the o54 goldens +
    _FT reference parity, then n tape PRs: time of kw15 alone and of the _FT pair."""
    import numpy as np
    from scipy.stats import spearmanr
    import o55_fm3_lane as o55                                # tape_rows only
    t0 = time.monotonic()
    ms = Kw15FtModelSet(intra_op=1, kinds=('kw15',)).load()
    state = ms.self_check()
    print(f"[kw15_FT] loaded in {time.monotonic() - t0:.1f}s — self-check: {state}")
    if not state_ok(ms.kw15ft_state):
        print("[kw15_FT] FAILED")
        return 1
    rows = o55.tape_rows(n)
    t_kw, t_ft, vals, bad = [], [], [], []
    for h, b in rows:
        r = ms.run('kw15', h, b)
        t_kw.append(r['_ms'])
        t_ft.append(r['_ms_ft'])
        v = [r.get(c) for c in ('kw15_proba_bd', 'kw15_proba_hd') + KW15FT_COLS]
        if not all(isinstance(x, float) and math.isfinite(x) and 0.0 <= x <= 1.0 for x in v):
            bad.append(h[:60])
        vals.append(v)
    p = lambda x: np.percentile(x, [50, 95, 99])              # noqa: E731
    a, b = p(t_kw), p(t_ft)
    V = np.asarray(vals, dtype=float)
    print(f"[kw15_FT] {len(rows)} tape PRs, 1 thread: kw15 p50/p95/p99 "
          f"{a[0]:.1f}/{a[1]:.1f}/{a[2]:.1f} ms | + _FT pair {b[0]:.1f}/{b[1]:.1f}/{b[2]:.1f} ms")
    print(f"[kw15_FT] Spearman kw15 vs _FT on these rows: body "
          f"{spearmanr(V[:, 0], V[:, 2]).correlation:.3f}, headline "
          f"{spearmanr(V[:, 1], V[:, 3]).correlation:.3f} | mean bd {V[:, 0].mean():.3f} -> "
          f"{V[:, 2].mean():.3f}, hd {V[:, 1].mean():.3f} -> {V[:, 3].mean():.3f}")
    if bad:
        print(f"[kw15_FT] FAILED: {len(bad)} row(s) with a value outside [0,1]: {bad[:5]}")
        return 1
    print("[kw15_FT] OK")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('check')
    c.add_argument('--n', type=int, default=200)
    a = ap.parse_args()
    sys.exit(cmd_check(a.n))


if __name__ == '__main__':
    main()
