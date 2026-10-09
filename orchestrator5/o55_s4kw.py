#!/usr/bin/env python
"""
o55_s4kw.py -- Orchestrator5.5's S4 kw columns: two more keyword pipelines, scored in the
SAME engine task as today's kw15 / kw15_FT, before the decision. Score-only: no rule reads
them, no trade impact.

    /home/tom/venv/bin/python o55_s4kw.py check [--n 200]   # goldens + reference parity + timing

THE MODELS (tools/keyword/S4_model_trained_on_2026-05-04-2026-09-24_val7030/, s4_ft_train.py)
  val7030_experiment.py's recipe (keyword_finder.py --task classification
  --no-strip-wire-boilerplate, tfidf only) refit on every labelled row of its channel -- same
  idea as kw15_FT, but two single-channel models at different thresholds instead of one
  model's head/body pair:
    kw30_tfidf_S4_FT_hd   headline pipeline, label fromPR_High(%) >= 30 (same text_hd input
                          as kw15_proba_hd / kw15_proba_hd_FT)
    kw50_tfidf_S4_FT_bd   body pipeline,     label fromPR_High(%) >= 50 (same text_bd400
                          input as kw15_proba_bd / kw15_proba_bd_FT)
  ⚠ NO held-out score: both trained on every labelled row. Rules MAY name the two fields
    (Orchestrator5.5 _TRIGGER_FIELDS) -- pick their cuts from live data only.
  Neither model shipped with a reference_scores.json (card.json's own note: "nothing loads
  this model live"); tools/keyword/s4_reference_gen.py wrote the one this module checks
  against.

FAIL-OPEN WHILE NO RULE READS THEM
  Missing files, a load error, or ANY digit off s4_reference_gen.py's reference_scores.json
  turns S4 off (state logged, cells blank); kw15/kw15_FT and trading carry on, and an error
  while scoring S4 never costs kw15/kw15_FT their values. Orchestrator5.5 refuses to start
  only when a loaded rule reads an S4 field and S4 is not 'ok' -- the XGB leg's policy.

REUSE, NOT A COPY: o55_kw15ft.Kw15FtModelSet / Kw15FtThreadEngine (which themselves reuse
o54_engine.ModelSet / ThreadEngine unchanged). Only ModelSet.load / _t_kw15 / self_check and
ThreadEngine.start are overridden here; o54_engine.py and o55_kw15ft.py are not modified.
⚠ S4 rides on the kw15_FT thread engine: `--no-kw15ft` (or `--engine procs`) also disables S4
  -- there is no kw15_FT-off/S4-on combination. `--no-s4` alone keeps kw15_FT running.
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
import o55_kw15ft as o55kw                        # noqa: E402

log = logging.getLogger('o55_s4kw')

S4_DIR = SCRIPTS_DIR / "tools" / "keyword" / "S4_model_trained_on_2026-05-04-2026-09-24_val7030"
S4_HD_PIPELINE = S4_DIR / "kw30_tfidf_S4_FT_hd" / "tfidf_fitted_pipeline.joblib"
S4_BD_PIPELINE = S4_DIR / "kw50_tfidf_S4_FT_bd" / "tfidf_fitted_pipeline.joblib"
# Written by s4_reference_gen.py from RAW Headline / Body cells through the live prep: the
# engine must reproduce every value exactly, which proves train/serve parity end to end.
S4_REFERENCE = S4_DIR / "reference_scores.json"
S4_COLS = ('kw30_tfidf_S4_FT_hd', 'kw50_tfidf_S4_FT_bd')


def state_ok(state: str) -> bool:
    return str(state).startswith('ok')


class S4ModelSet(o55kw.Kw15FtModelSet):
    """o55_kw15ft.Kw15FtModelSet + the two S4 pipelines inside the SAME kw15 task."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.s4_hd = self.s4_bd = None
        self.s4_state = 'not_loaded'

    @property
    def s4_on(self) -> bool:
        return self.s4_hd is not None and self.s4_bd is not None

    def load(self):
        # BEFORE super().load(): its warm() runs the kw15 task, which then warms S4 too.
        if 'kw15' in self.kinds:
            try:
                import joblib
                self.s4_hd = joblib.load(S4_HD_PIPELINE)
                self.s4_bd = joblib.load(S4_BD_PIPELINE)
                self.s4_state = 'loaded'
            except Exception as exc:                          # noqa: BLE001
                self._s4_off(f"load failed: {type(exc).__name__}: {exc}")
        return super().load()

    def _s4_off(self, reason: str) -> None:
        self.s4_hd = self.s4_bd = None
        self.s4_state = f"off ({reason})"

    def _t_kw15(self, headline: str, body_cell: str) -> dict:
        """Kw15FtModelSet._t_kw15 (kw15 + kw15_FT, unchanged), then the S4 pair on the SAME
        prep this class already knows (_kw15_prep). A separate, sequential lock acquisition
        -- the base call has already released self._kw_lock by the time this runs, so there
        is no nested/reentrant lock use."""
        r = super()._t_kw15(headline, body_cell)
        r.update(self._s4_pair(headline, body_cell))
        return r

    def _t_kw_rest(self, headline: str, body_cell: str) -> dict:
        """Plain kw15 (base), then the S4 pair -- the late-stage half since 2026-10-07."""
        r = super()._t_kw_rest(headline, body_cell)
        r.update(self._s4_pair(headline, body_cell))
        return r

    def _s4_pair(self, headline: str, body_cell: str) -> dict:
        hd_val = bd_val = ms_s4 = s4_error = None
        if self.s4_on:
            with self._kw_lock:
                t0 = time.perf_counter()
                try:
                    head_txt = self._kw15_prep(headline, None)
                    body_txt = self._kw15_prep(body_cell, o54.KW15_BODY_MAX_WORDS)
                    hd_val = float(round(float(
                        self.s4_hd.predict_proba([head_txt])[0][1]), 6))
                    bd_val = float(round(float(
                        self.s4_bd.predict_proba([body_txt])[0][1]), 6))
                    ms_s4 = round((time.perf_counter() - t0) * 1000.0, 1)
                except Exception as exc:                      # noqa: BLE001
                    hd_val = bd_val = None
                    s4_error = f"{type(exc).__name__}: {exc}"
        return {'kw30_tfidf_S4_FT_hd': hd_val, 'kw50_tfidf_S4_FT_bd': bd_val,
                '_ms_s4': ms_s4, '_s4_error': s4_error}

    def self_check(self, path: Path = o54.GOLDENS_FILE, required: bool = True) -> str:
        """kw15 goldens + kw15_FT reference (base, unchanged), then S4 against its own
        reference_scores.json -- fail-open."""
        base = super().self_check(path, required)
        if 'kw15' in self.kinds and self.s4_on:
            self.s4_state = self._check_s4_reference()
        return f"{base}; S4 {self.s4_state}"

    def _check_s4_reference(self) -> str:
        try:
            ref = json.loads(S4_REFERENCE.read_text(encoding='utf-8'))
            bad = []
            for r in ref['rows']:
                got = self._t_kw15(r['Headline'], r['Body'])
                if got.get('_s4_error'):
                    raise RuntimeError(got['_s4_error'])
                bad += [f"{r['ID']}.{c}: {got.get(c)} != {r[c]}"
                        for c in S4_COLS if got.get(c) != r[c]]
            if bad:
                raise ValueError(f"{len(bad)} value(s) off the reference: "
                                 + ' | '.join(bad[:4]))
            return f"ok ({len(ref['rows'])} reference rows exact)"
        except Exception as exc:                              # noqa: BLE001
            self._s4_off(f"{type(exc).__name__}: {exc}")
            return self.s4_state


class S4ThreadEngine(o55kw.Kw15FtThreadEngine):
    """o55_kw15ft.Kw15FtThreadEngine with S4ModelSet: start() is the base one verbatim except
    for the ModelSet class (same pools, same routing, same single kw15 thread)."""

    def start(self):
        self.models = S4ModelSet(self.intra_op, self.spinning).load()
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
                spinning=False, goldens_required=True, logger=None, kw15ft=True, s4=True):
    """o55_kw15ft.make_engine, with the S4 ModelSet on top when both kw15_FT and S4 are on.
    s4=False (--no-s4) keeps kw15_FT running via the plain Kw15FtThreadEngine; kw15ft=False
    (--no-kw15ft) or kind='procs' falls through to o54's plain engine, so S4 is off too --
    there is no kw15_FT-off/S4-on combination."""
    if kind == 'threads' and kw15ft and s4:
        return S4ThreadEngine(pools=pools, workers=workers, intra_op=intra_op,
                              spinning=spinning, goldens_required=goldens_required)
    return o55kw.make_engine(kind=kind, pools=pools, workers=workers, intra_op=intra_op,
                             cpus=cpus, spinning=spinning, goldens_required=goldens_required,
                             logger=logger, kw15ft=kw15ft)


def engine_state(engine, enabled: bool = True) -> str:
    """'ok (...)' | 'off (...)' | 'disabled (--no-s4)' | 'unsupported (...)'."""
    if not enabled:
        return 'disabled (--no-s4)'
    if isinstance(engine, S4ThreadEngine):
        return engine.models.s4_state if engine.models else 'not_loaded'
    return "unsupported (kw15_FT off, or procs engine: its workers run o54_engine's own ModelSet)"


def disable(engine, reason: str) -> str:
    """Turn S4 off in a running engine (fail-open) and return the new state."""
    models = getattr(engine, 'models', None)
    if isinstance(models, S4ModelSet):
        models._s4_off(reason)
        return models.s4_state
    return engine_state(engine)


# ─── check ────────────────────────────────────────────────────────────────────

def cmd_check(n: int = 200) -> int:
    """kw15 alone, as the engine runs it (kinds=('kw15',), this process): kw15 + kw15_FT +
    S4 goldens/reference checks, then n tape PRs: time of each stage."""
    import numpy as np
    from scipy.stats import spearmanr
    import o55_fm3_lane as o55                                # tape_rows only
    t0 = time.monotonic()
    ms = S4ModelSet(intra_op=1, kinds=('kw15',)).load()
    state = ms.self_check()
    print(f"[S4] loaded in {time.monotonic() - t0:.1f}s — self-check: {state}")
    if not state_ok(ms.s4_state):
        print("[S4] FAILED")
        return 1
    rows = o55.tape_rows(n)
    t_kw, t_ft, t_s4, vals, bad = [], [], [], [], []
    t_split, split_bad = [], []
    for h, b in rows:
        r = ms.run('kw15', h, b)
        t_kw.append(r['_ms'])
        t_ft.append(r['_ms_ft'])
        t_s4.append(r['_ms_s4'])
        v = [r.get(c) for c in ('kw15_proba_bd', 'kw15_proba_hd') + o55kw.KW15FT_COLS + S4_COLS]
        # The 2026-10-07 split must give the `kw15` task's values, digit for digit.
        t = time.perf_counter()
        split = ms.run('kw_ft', h, b)
        t_split.append((time.perf_counter() - t) * 1000.0)
        split.update(ms.run('kw_rest', h, b))
        split_bad += [f"{h[:40]}.{c}" for c in ('kw15_proba_bd', 'kw15_proba_hd')
                      + o55kw.KW15FT_COLS + S4_COLS if split.get(c) != r.get(c)]
        if not all(isinstance(x, float) and math.isfinite(x) and 0.0 <= x <= 1.0 for x in v):
            bad.append(h[:60])
        vals.append(v)
    p = lambda x: np.percentile(x, [50, 95, 99])              # noqa: E731
    a, b_, c_ = p(t_kw), p(t_ft), p(t_s4)
    V = np.asarray(vals, dtype=float)
    print(f"[S4] {len(rows)} tape PRs, 1 thread: kw15 p50/p95/p99 "
          f"{a[0]:.1f}/{a[1]:.1f}/{a[2]:.1f} ms | + kw15_FT pair {b_[0]:.1f}/{b_[1]:.1f}/{b_[2]:.1f} ms"
          f" | + S4 pair {c_[0]:.1f}/{c_[1]:.1f}/{c_[2]:.1f} ms")
    print(f"[S4] Spearman kw15_hd vs kw30_S4_hd: "
          f"{spearmanr(V[:, 1], V[:, 4]).correlation:.3f}, kw15_bd vs kw50_S4_bd: "
          f"{spearmanr(V[:, 0], V[:, 5]).correlation:.3f} | mean hd {V[:, 4].mean():.3f}, "
          f"bd {V[:, 5].mean():.3f}")
    d = p(t_split)
    print(f"[S4] decision-path kw_ft task alone (wall, incl. prep) p50/p95/p99 "
          f"{d[0]:.1f}/{d[1]:.1f}/{d[2]:.1f} ms | kw_ft + kw_rest == kw15 task: "
          f"{'ok' if not split_bad else f'{len(split_bad)} MISMATCH(ES)'}")
    if split_bad:
        print(f"[S4] FAILED: the kw_ft/kw_rest split differs from the kw15 task: {split_bad[:5]}")
        return 1
    if bad:
        print(f"[S4] FAILED: {len(bad)} row(s) with a value outside [0,1]: {bad[:5]}")
        return 1
    print("[S4] OK")
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
