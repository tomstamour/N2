#!/usr/bin/env python
"""
o54_engine.py -- Orchestrator5.4's scoring engine: the per-article model tasks, and the
two ways of running them (threads in the orchestrator's process, or pinned worker
processes).

WHY THIS IS A SEPARATE MODULE
  * Worker processes need an importable, side-effect-free entry point. Spawning through
    `multiprocessing` would re-execute Orchestrator5.4.py's module-level code (daily-log
    handler, pools, heavy imports) in every child, so workers are launched as
    `python o54_engine.py --worker ...` subprocesses instead.
  * bench/o54_bench.py must measure the code that runs live, not a copy of it.

THE TASKS (all pure: they take strings/dicts, return dicts, log nothing)
  'prosus' 'fmb' 'flang'   the three GATE headline models (ProsusAI FinBERT, FinMarBa,
                           FLANG-BERT), called through their frozen headliner modules'
                           own public functions -- same outputs as 5.3's analyze_*.
  'kw15'                   the two corrected-label ge15 tfidf pipelines (5.3's
                           _score_keywords, same text prep, same 6-dp rounding).
  'embed'                  model-3's only model call: the INT8 FinBERT embedding of the
                           headline, projected onto model-3's own PCA basis, 6 dp.
  'xgb'                    the model-3 / model-3FT boosters for ONE ticker, on a raw
                           feature row the orchestrator builds; only the requested
                           columns are predicted.

THE ENGINES (both return concurrent.futures.Future from submit(prio, kind, *args))
  ThreadEngine   'shared': one priority pool of N threads for every model task (kw15
                           keeps its own single thread, as in 5.3).
                 'per-model': 5.3's layout -- one pool per headline model, one for the
                           leg -- kept so the benchmark can measure the old arrangement.
  ProcessEngine  N subprocess workers, each pinned to its own CPU(s), each holding a full
                 ModelSet; one feeder thread per worker pulls from a shared priority
                 queue. A dead or hung worker fails its in-flight future (the gate then
                 reads MISSING -> BAD, with the existing alarm) and is respawned.

THREAD CONTROL WITHOUT EDITING THE FROZEN HEADLINERS
  The headliner modules build their ORT sessions with default options. ModelSet lets
  each module load itself normally, then (only when intra_op is set) swaps the
  Inferencer's `model` for an identical ORTModel built with SessionOptions
  (intra_op_num_threads, spinning). The module's own analyze_headline/score_headline and
  its predict() -- tokenizer call, softmax, rounding, label map -- are what run. A thread
  count must not change a single output digit: bench/o54_bench.py `parity` and the
  goldens self-check (bench/o54_goldens.json) enforce that at 4 dp (models) / 6 dp (PCA).

INVARIANTS INHERITED FROM MODEL-3 (training_data/model-3/CLAUDE.md)
  * headline = Headline.strip(); ONE text per INT8 forward, never batched (dynamic INT8
    takes its activation scale from the whole tensor, padding included).
  * PCA with model-3's own basis, values ROUNDED TO 6 DP (the training table's precision).
  * boosters are fixed-round refits: every tree, range cached at load, nthread=1.
  * feature order comes from prep['features_used'] via build_row_features, never by hand.
"""

import argparse
import hashlib
import importlib.util as _ilu
import itertools
import json
import logging
import math
import os
import queue
import signal
import socket
import subprocess
import sys
import threading
import time
import traceback
from concurrent.futures import Future
from pathlib import Path

HERE = Path(__file__).resolve().parent            # orchestrator5/
SCRIPTS_DIR = HERE.parent                         # N2/scripts

log = logging.getLogger('o54_engine')

# ─── Artifacts ────────────────────────────────────────────────────────────────
# model-3 (training_data/model-3). Every file below is gitignored and lives only on
# this machine; the md5s pin the exact bytes the training corpus was embedded with.
M3_DIR       = SCRIPTS_DIR / "training_data" / "model-3"
M3_CONFIGS   = {'m3':   M3_DIR / "configs" / "int8_c200_s04",
                'm3FT': M3_DIR / "configs" / "int8_c200_s04_FT"}
M3_GEN       = "m3:int8_c200_s04"
M3_EMBED_DIR = M3_DIR / "embedder_int8_matmul"
M3_PCA_PKL   = M3_DIR / "pca" / "int8_cap200" / "finbert_pca.pkl"
M3_EMBED_MD5 = "07054796b3cad6ee30be3e8b7354e9e7"   # model.onnx the corpus was embedded with
M3_PCA_MD5   = "26f4ccd4b3351c31dea012b0bf5ab177"
M3_PCA_WIDTH = 40

# The ten boosters. The FT twin is scored and logged but no rule may rely on it being
# honest: it trained on every row, so it has no held-out score (FT_report.md).
XGB_MODELS = (
    # (TSV column,  config key, .ubj stem,  kind)
    ('se_m3',      'm3',   'xgb_se',   'regressor'),
    ('q80_m3',     'm3',   'xgb_q80',  'regressor'),
    ('ge20_m3',    'm3',   'xgb_ge20', 'classifier'),
    ('ge35_m3',    'm3',   'xgb_ge35', 'classifier'),
    ('ge50_m3',    'm3',   'xgb_ge50', 'classifier'),
    ('se_m3FT',    'm3FT', 'xgb_se',   'regressor'),
    ('q80_m3FT',   'm3FT', 'xgb_q80',  'regressor'),
    ('ge20_m3FT',  'm3FT', 'xgb_ge20', 'classifier'),
    ('ge35_m3FT',  'm3FT', 'xgb_ge35', 'classifier'),
    ('ge50_m3FT',  'm3FT', 'xgb_ge50', 'classifier'),
)
XGB_COLS = tuple(m[0] for m in XGB_MODELS)
XGB_INFERENCE_SCRIPT = SCRIPTS_DIR / "training_data" / "XGBoost_inference_script.py"
XGB_SENTINEL = 44444.0          # verified against the script's own SENTINEL at load

# The two corrected-label ge15 keyword pipelines (unchanged from 5.3).
KW15_DIR = (SCRIPTS_DIR / "tools" / "keyword" /
            "model_trained_on_04-PRstats-2026-05-04to06-18_TRAIN")
KW15_BODY_PIPELINE = KW15_DIR / "classify_fromPRhigh_ge15" / "tfidf_fitted_pipeline.joblib"
KW15_HEAD_PIPELINE = KW15_DIR / "classify_fromPRhigh_ge15_headline" / "tfidf_fitted_pipeline.joblib"
# ⚠ Not optional: the corpus bodies were capped at 400 words upstream; dropping the cap
#   flips 49 of 777 VAL fire decisions (verified 2026-07-28). See 5.3's KW15 notes.
KW15_BODY_MAX_WORDS = 400

# The three gate headliners: (module name, file, Inferencer class, public fn).
# The module names are 5.3's, so the singletons never collide with each other.
HEADLINERS = {
    'prosus': ("FinBERT_headliner", SCRIPTS_DIR / "FinBERT" / "FinBERT-headliner.py",
               "FinBERTInferencer", "analyze_headline"),
    'fmb':    ("FinMarBa_headliner", SCRIPTS_DIR / "tools" / "Encoder_Models" / "FinMarBa_headliner.py",
               "FinMarBaInferencer", "score_headline"),
    'flang':  ("FLANG_BERT_headliner", SCRIPTS_DIR / "tools" / "Encoder_Models" / "FLANG-BERT_headliner.py",
               "FlangBertInferencer", "analyze_headline"),
}

GOLDENS_FILE = HERE / "bench" / "o54_goldens.json"

# Queue priorities. LOWER IS SERVED FIRST; FIFO within one priority.
PRIO_TRADEABLE = 0    # a STEP-1b decision is waiting on this result
PRIO_CORPUS    = 1    # TSV-only: post-decision work, or a PR that can never be sent STEP-1b
PRIO_SHUTDOWN  = 9    # drains everything queued before it

TASK_KINDS = ('prosus', 'fmb', 'flang', 'kw15', 'embed', 'xgb')
DEFAULT_INFER_CPUS = (1, 2, 3, 5, 6, 7)   # core 0 (CPUs 0,4) reserved, operator 2026-09-24

WORKER_READY_TIMEOUT_S = 300.0   # model load in a cold worker, all workers in parallel
WORKER_TASK_TIMEOUT_S  = 30.0    # a single task taking this long = hung worker -> respawn


# ─── Small helpers ────────────────────────────────────────────────────────────

def load_by_path(name: str, path: Path, register: bool = False):
    """importlib by file path (hyphenated filenames, non-package dirs)."""
    spec = _ilu.spec_from_file_location(name, str(path))
    mod = _ilu.module_from_spec(spec)
    if register:
        sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def md5_file(path: Path) -> str:
    h = hashlib.md5()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def check_m3_artifacts() -> None:
    """Hard-fail unless the INT8 embedder and the PCA basis are byte-identical to the
    ones model-3 was trained on. A mismatch would put every hd_pca_* off-basis."""
    onnx = M3_EMBED_DIR / "model.onnx"
    if not onnx.is_file():
        raise RuntimeError(f"model-3 INT8 embedder missing: {onnx}")
    if md5_file(onnx) != M3_EMBED_MD5:
        raise RuntimeError("model-3 INT8 embedder is not the one the corpus was embedded "
                           "with (md5 mismatch) — every hd_pca_* would be off-basis. "
                           "Rebuild with m3_int8_embedder.py only if the rebuild is "
                           "byte-identical; otherwise retrain.")
    if not M3_PCA_PKL.is_file():
        raise RuntimeError(f"model-3 PCA basis missing: {M3_PCA_PKL}")
    if md5_file(M3_PCA_PKL) != M3_PCA_MD5:
        raise RuntimeError("model-3 PCA basis md5 mismatch — refusing to start")


def parse_cpus(spec) -> tuple:
    """'1-3,5-7' -> (1,2,3,5,6,7); 'all' -> every online CPU; a tuple passes through."""
    if spec is None:
        return tuple(DEFAULT_INFER_CPUS)
    if isinstance(spec, (tuple, list, set, frozenset)):
        return tuple(sorted(int(c) for c in spec))
    spec = str(spec).strip().lower()
    if spec == 'all':
        return tuple(range(os.cpu_count() or 1))
    out = set()
    for part in spec.split(','):
        part = part.strip()
        if not part:
            continue
        if '-' in part:
            a, b = part.split('-', 1)
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    if not out:
        raise ValueError(f"empty CPU list {spec!r}")
    return tuple(sorted(out))


def _read(p):
    try:
        return Path(p).read_text().strip()
    except Exception:
        return None


def sibling_groups(cpus) -> list:
    """Group `cpus` by physical core (hyperthread siblings), from sysfs.
    On this i7-1185G7 CPU c and c+4 share one core."""
    cpus = set(cpus)
    seen, groups = set(), []
    for c in sorted(cpus):
        if c in seen:
            continue
        sib = _read(f"/sys/devices/system/cpu/cpu{c}/topology/thread_siblings_list")
        members = set(parse_cpus(sib)) & cpus if sib else {c}
        members.add(c)
        seen |= members
        groups.append(tuple(sorted(members)))
    return groups


def plan_worker_cpus(workers: int, cpus) -> list:
    """One CPU set per worker process.
      workers == #CPUs          -> one logical CPU each
      workers == #physical cores -> one core (both hyperthreads) each
      anything else             -> every worker may use every CPU in `cpus`."""
    cpus = tuple(sorted(cpus))
    if workers == len(cpus):
        return [(c,) for c in cpus]
    groups = sibling_groups(cpus)
    if workers == len(groups):
        return groups
    return [cpus] * workers


def read_power_state() -> dict:
    """CPU power-management state, for the [Engine] startup block (world-readable)."""
    import glob as _glob
    epp = sorted({_read(f) for f in _glob.glob(
        "/sys/devices/system/cpu/cpu[0-9]*/cpufreq/energy_performance_preference")} - {None})
    gov = sorted({_read(f) for f in _glob.glob(
        "/sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_governor")} - {None})
    return {
        'platform_profile': _read("/sys/firmware/acpi/platform_profile"),
        'epp': epp,
        'governor': gov,
        'min_perf_pct': _read("/sys/devices/system/cpu/intel_pstate/min_perf_pct"),
        'no_turbo': _read("/sys/devices/system/cpu/intel_pstate/no_turbo"),
    }


def power_state_is_performance(state: dict) -> bool:
    return (state.get('platform_profile') == 'performance'
            and state.get('epp') == ['performance'])


# ─── model-3 XGBoost artifacts (preps only — no boosters) ─────────────────────

def _check_m3_config(key: str, cfg_dir: Path) -> dict:
    """Provenance: refuse any config that is not the INT8 / matmul / headline-only
    model-3 this module was written for."""
    cfg = json.loads((cfg_dir / "config.json").read_text(encoding='utf-8'))
    problems = []
    if cfg.get('arm') != 'int8' or cfg.get('int8_recipe') != 'matmul':
        problems.append(f"arm={cfg.get('arm')!r} int8_recipe={cfg.get('int8_recipe')!r} "
                        f"(want int8 / matmul)")
    if Path(cfg.get('pca_pkl', '')).resolve() != M3_PCA_PKL.resolve():
        problems.append(f"pca_pkl={cfg.get('pca_pkl')!r} (want {M3_PCA_PKL})")
    if Path(cfg.get('embed_dir', '')).resolve() != M3_EMBED_DIR.resolve():
        problems.append(f"embed_dir={cfg.get('embed_dir')!r} (want {M3_EMBED_DIR})")
    if list(cfg.get('groups') or []) != ['emb:hd']:
        problems.append(f"groups={cfg.get('groups')!r} — this loader reads NO body; a "
                        f"config with a body group needs a different leg")
    if bool(cfg.get('full_train', False)) != (key == 'm3FT'):
        problems.append(f"full_train={cfg.get('full_train')!r} for the {key!r} slot")
    if problems:
        raise RuntimeError(f"model-3 config {cfg_dir.name}: " + '; '.join(problems))
    return cfg


def load_xgb_preps() -> list:
    """Read + validate the ten _prep.json files. Returns a list of dicts
      {col, cfg, stem, kind, prep, group, ubj}
    where `group` indexes the DISTINCT preprocessings (m3 and m3FT share features but not
    winsor bounds -> 2 groups; discovered by signature, as in 5.3). Raises on anything
    wrong: missing files, no features_used, feature lists that differ between heads."""
    for key, d in M3_CONFIGS.items():
        _check_m3_config(key, d)
    entries, groups, features = [], {}, None
    for col, key, stem, kind in XGB_MODELS:
        d = M3_CONFIGS[key]
        ubj, prep_path = d / f"{stem}.ubj", d / f"{stem}_prep.json"
        if not ubj.is_file():
            raise RuntimeError(f"XGBoost model missing: {ubj}")
        if not prep_path.is_file():
            raise RuntimeError(f"XGBoost prep missing: {prep_path}")
        prep = json.loads(prep_path.read_text(encoding='utf-8'))
        if 'features_used' not in prep:
            raise RuntimeError(f"{prep_path} has no 'features_used' — cannot score live")
        if 'n_rounds' not in prep:
            raise RuntimeError(f"{prep_path} has no 'n_rounds' — not a model-3 prep")
        if features is None:
            features = list(prep['features_used'])
        elif list(prep['features_used']) != features:
            raise RuntimeError(f"{key}/{stem}: features_used differs from the other heads "
                               f"— the m3 and m3FT feature lists must be identical")
        sig = json.dumps([prep['features_used'], prep['cat_cols'], prep['categories'],
                          prep['sentinel_high'], prep['sentinel_zero'], prep['winsor']],
                         sort_keys=True)
        if sig not in groups:
            groups[sig] = len(groups)
        entries.append({'col': col, 'cfg': key, 'stem': stem, 'kind': kind, 'prep': prep,
                        'group': groups[sig], 'ubj': ubj})
    return entries


def synthetic_raw_row(features) -> dict:
    """A mid-range raw feature row for smoke checks and goldens. Deliberately NOT zeros,
    so a winsor bound or a categorical mapping that silently drops everything still
    produces a distinguishable number."""
    special = {'FineClass': 'MA', 'Float': 5.0, 'MktCap': 20.0, 'TimeToOpen': 1.5,
               'Prosus_ss_hd.1': 0.5, 'FLANG_ss_hd.1': 0.5, 'FLANG_neu_hd.1': 0.3,
               'FinMarBa_ind_hd.1': 0.1}
    raw = {}
    for f in features:
        if f.startswith('hd_pca_') or f.startswith('bd_pca_'):
            continue
        raw[f] = special.get(f, 1.0)
    raw.update({'RTH_avgITI_sec': 10.0, 'ETH_avgITI_sec': 30.0, 'RTH_tradeSize': 500.0})
    return raw


# ─── ModelSet: everything one process needs to run the tasks ─────────────────

class ModelSet:
    """The loaded models of ONE process. Only one may exist per process: the three
    headliner modules keep module-level singletons.

    intra_op  None -> ORT's default session options (exactly 5.3's sessions)
              N    -> intra_op_num_threads=N on every ONNX session this set owns
    spinning  only matters for intra_op > 1: False sets session.intra_op.allow_spinning=0
              so an idle pool thread sleeps instead of burning a core another worker needs.
    """

    def __init__(self, intra_op=None, spinning=False, kinds=TASK_KINDS):
        self.intra_op = int(intra_op) if intra_op else None
        self.spinning = bool(spinning)
        self.kinds = tuple(kinds)
        unknown = set(self.kinds) - set(TASK_KINDS)
        if unknown:
            raise ValueError(f"unknown task kind(s) {sorted(unknown)}")
        self.mods = {}
        self.loaded = False
        self.load_s = None
        self._kw_lock = threading.Lock()   # sklearn: serialize, as 5.3's single kw worker

    # ── loading ────────────────────────────────────────────────────────────────
    def _session_options(self):
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.intra_op_num_threads = self.intra_op
        so.inter_op_num_threads = 1
        if self.intra_op > 1 and not self.spinning:
            so.add_session_config_entry("session.intra_op.allow_spinning", "0")
        return so

    def _load_headliner(self, kind: str):
        mod_name, path, cls_name, _fn = HEADLINERS[kind]
        mod = load_by_path(mod_name, path)
        model_dir = mod.MODEL_DIR
        if not Path(model_dir).exists():
            raise FileNotFoundError(f"ONNX model directory not found: {model_dir}")
        inf = getattr(mod, cls_name)(model_dir)
        inf.load()                      # the module's OWN load: tokenizer, label map, session
        if self.intra_op is not None:
            # Same export, same provider, only the thread options differ. Outputs are
            # checked digit-for-digit against the default session (goldens / parity).
            from optimum.onnxruntime import ORTModelForSequenceClassification
            inf.model = ORTModelForSequenceClassification.from_pretrained(
                str(model_dir), session_options=self._session_options())
        # Install as the module singleton so its public function is what runs.
        mod._inferencer, mod._model_dir = inf, model_dir
        self.mods[kind] = mod

    def load(self):
        t0 = time.perf_counter()
        for kind in ('prosus', 'fmb', 'flang'):
            if kind in self.kinds:
                self._load_headliner(kind)
        if 'kw15' in self.kinds:
            import joblib
            kw2 = load_by_path("keyword_scorer_2",
                               SCRIPTS_DIR / "tools" / "keyword" / "keyword_scorer-2.py")
            from keyword_finder import strip_wire_boilerplate   # kw2 put it on sys.path
            self._kw2_strip_html = kw2._strip_html
            self._strip_wire = strip_wire_boilerplate
            self.kw_body = joblib.load(KW15_BODY_PIPELINE)
            self.kw_head = joblib.load(KW15_HEAD_PIPELINE)
        if 'embed' in self.kinds:
            import joblib
            import numpy as np
            self._np = np
            emb_mod = load_by_path("o54_m3_embedder", M3_DIR / "m3_embedder.py")
            self.embedder = emb_mod.Embedder(M3_EMBED_DIR)
            if self.intra_op is not None:
                from optimum.onnxruntime import ORTModelForFeatureExtraction
                self.embedder.model = ORTModelForFeatureExtraction.from_pretrained(
                    str(M3_EMBED_DIR), session_options=self._session_options())
            pca = joblib.load(M3_PCA_PKL)
            hd = pca['hd']
            self.pca_mean = np.asarray(hd['mean_'], dtype=np.float64)
            self.pca_comp_t = np.asarray(hd['components_'], dtype=np.float64).T
            if self.pca_mean.shape != (768,) or self.pca_comp_t.shape != (768, M3_PCA_WIDTH):
                raise RuntimeError(f"model-3 PCA has the wrong shape: mean "
                                   f"{self.pca_mean.shape}, components^T "
                                   f"{self.pca_comp_t.shape}")
        if 'xgb' in self.kinds:
            self._load_xgb()
        self.loaded = True
        self.load_s = round(time.perf_counter() - t0, 1)
        # Warm every loaded path once so no live PR pays first-call kernel/JIT cost.
        self.warm()
        return self

    def _load_xgb(self):
        import xgboost as xgb
        self._xgb = xgb
        inf = load_by_path("o54_xgb_inference", XGB_INFERENCE_SCRIPT)
        if float(inf.SENTINEL) != XGB_SENTINEL:
            raise RuntimeError(f"XGBoost_inference_script.SENTINEL is {inf.SENTINEL}, "
                               f"expected {XGB_SENTINEL}")
        self._build_row_features = inf.build_row_features
        self._invert_target = inf._invert_target
        entries = load_xgb_preps()
        self.xgb_models, orders = [], {}
        for e in entries:
            b = xgb.Booster()
            b.load_model(str(e['ubj']))
            nr = b.num_boosted_rounds()
            if nr != int(e['prep']['n_rounds']):
                raise RuntimeError(f"{e['cfg']}/{e['stem']}: {nr} boosted rounds, prep says "
                                   f"{e['prep']['n_rounds']} — wrong artifact")
            best = getattr(b, 'best_iteration', None)
            if best is not None and int(best) + 1 != nr:
                raise RuntimeError(f"{e['cfg']}/{e['stem']}: early-stopped booster "
                                   f"(best_iteration={best} of {nr}) — model-3 boosters are "
                                   f"fixed-round refits; this loader predicts every tree")
            # ONCE, at load: xgboost 3.4.1 can raise from num_boosted_rounds() on the
            # quantile booster after some predict sequences.
            it_range = (0, nr)
            # One thread: a single-row predict cannot use more, and right after an ONNX
            # call ORT's spinning pool starves XGBoost's (68 -> 21 ms, same values).
            b.set_param({'nthread': 1})
            orders.setdefault(e['group'], e['prep'])
            self.xgb_models.append((e['col'], b, e['prep'], e['kind'], it_range, e['group']))
        self.xgb_orders = orders
        self.xgb_features = list(entries[0]['prep']['features_used'])
        self.xgb_groups = len(orders)

    def warm(self):
        hd = "Acme Corp announces FDA approval of its lead drug candidate"
        for kind in ('prosus', 'fmb', 'flang'):
            if kind in self.mods:
                self.run(kind, hd)
        if 'kw15' in self.kinds:
            self.run('kw15', "startup self-check", "startup self-check")
        emb = None
        if 'embed' in self.kinds:
            emb = self.run('embed', hd)
        if 'xgb' in self.kinds:
            pca = emb['hd_pca'] if emb else [0.0] * M3_PCA_WIDTH
            self.run('xgb', synthetic_raw_row(self.xgb_features), pca, None)

    # ── the tasks ──────────────────────────────────────────────────────────────
    def run(self, kind: str, *args):
        if kind == 'prosus':
            return self._t_prosus(*args)
        if kind == 'fmb':
            return self._t_fmb(*args)
        if kind == 'flang':
            return self._t_flang(*args)
        if kind == 'kw15':
            return self._t_kw15(*args)
        if kind == 'embed':
            return self._t_embed(*args)
        if kind == 'xgb':
            return self._t_xgb(*args)
        if kind == 'ping':
            return {'pid': os.getpid(), 'cpus': sorted(os.sched_getaffinity(0))}
        raise ValueError(f"unknown task kind {kind!r}")

    def _t_prosus(self, headline: str) -> dict:
        r = self.mods['prosus'].analyze_headline(headline)
        return {'pos': r['pos'], 'neg': r['neg'], 'neu': r['neu'], 'ss': r['ss'],
                'lab': r['lab'], 'ms': r['ms']}

    def _t_fmb(self, headline: str) -> dict:
        r = self.mods['fmb'].score_headline(headline)
        return {'pos': r['pos'], 'ind': r['ind'], 'neg': r['neg'], 'ss': r['ss'],
                'lab': r['lab'], 'ms': r['ms']}

    def _t_flang(self, headline: str) -> dict:
        r = self.mods['flang'].analyze_headline(headline)
        return {'pos': r['pos'], 'neu': r['neu'], 'neg': r['neg'], 'ss': r['ss'],
                'lab': r['lab'], 'ms': r['ms']}

    def _kw15_prep(self, text: str, max_words):
        """The EXACT training prep (5.3's _kw15_prep): strip HTML, strip wire
        boilerplate, then optionally cap."""
        out = self._strip_wire(self._kw2_strip_html(text or ''))
        return ' '.join(out.split()[:max_words]) if max_words else out

    def _t_kw15(self, headline: str, body_cell: str) -> dict:
        """kw15 on EXACTLY the TSV Body cell string (clean_inline_text(body_raw)) and the
        plain headline, 6 dp — 5.3's _score_keywords minus its logging."""
        with self._kw_lock:
            t0 = time.perf_counter()
            body_p = self.kw_body.predict_proba(
                [self._kw15_prep(body_cell, KW15_BODY_MAX_WORDS)])[0][1]
            head_p = self.kw_head.predict_proba(
                [self._kw15_prep(headline, None)])[0][1]
            ms = (time.perf_counter() - t0) * 1000.0
        return {'kw15_proba_bd': float(round(float(body_p), 6)),
                'kw15_proba_hd': float(round(float(head_p), 6)),
                '_ms': round(ms, 1), '_done_epoch': time.time()}

    def _t_embed(self, headline: str) -> dict:
        """model-3's ONLY model call. ⚠ ONE text per forward, never batched; PCA 6 dp."""
        t0 = time.perf_counter()
        vec = self.embedder.embed_one((headline or '').strip()).astype(self._np.float64)
        hd_pca = [round(float(x), 6) for x in (vec - self.pca_mean) @ self.pca_comp_t]
        return {'hd_pca': hd_pca,
                '_embed_ms': round((time.perf_counter() - t0) * 1000.0, 1)}

    def _t_xgb(self, raw_row: dict, hd_pca, cols=None) -> dict:
        """Predict `cols` (None = all ten) for ONE ticker. `raw_row` maps every non-PCA
        model feature (trained names, '.1' suffixes included) plus the three sentinel
        columns to RAW values; build_row_features does the casting, sentinel
        substitution, winsorisation and categoricals, exactly as offline."""
        want = set(XGB_COLS if cols is None else cols)
        need_groups = sorted({g for c, _b, _p, _k, _r, g in self.xgb_models if c in want})
        t0 = time.perf_counter()
        frames = {g: self._xgb.DMatrix(
                      self._build_row_features(raw_row, self.xgb_orders[g], hd_pca, None),
                      enable_categorical=True, nthread=1)
                  for g in need_groups}
        feat_ms = (time.perf_counter() - t0) * 1000.0
        t1 = time.perf_counter()
        out = {}
        for col, booster, prep, kind, it_range, g in self.xgb_models:
            if col not in want:
                continue
            raw_pred = float(booster.predict(frames[g], iteration_range=it_range)[0])
            out[col] = round(self._invert_target(raw_pred, prep)
                             if kind == 'regressor' else raw_pred, 6)
        out['_feat_ms'] = round(feat_ms, 1)
        out['_predict_ms'] = round((time.perf_counter() - t1) * 1000.0, 2)
        return out

    # ── goldens ────────────────────────────────────────────────────────────────
    def compute_goldens(self, probes, body: str) -> dict:
        """Deterministic outputs for a fixed probe set, per task kind this set owns."""
        out = {}
        for kind in ('prosus', 'fmb', 'flang'):
            if kind in self.mods:
                out[kind] = [{k: v for k, v in self.run(kind, p).items() if k != 'ms'}
                             for p in probes]
        if 'kw15' in self.kinds:
            out['kw15'] = [[r['kw15_proba_bd'], r['kw15_proba_hd']]
                           for r in (self.run('kw15', p, body) for p in probes)]
        embs = None
        if 'embed' in self.kinds:
            embs = [self.run('embed', p)['hd_pca'] for p in probes]
            out['embed'] = embs
        if 'xgb' in self.kinds:
            pca = embs[0] if embs else [0.0] * M3_PCA_WIDTH
            r = self.run('xgb', synthetic_raw_row(self.xgb_features), pca, None)
            out['xgb'] = {c: r[c] for c in XGB_COLS}
        return out

    def self_check(self, path: Path = GOLDENS_FILE, required: bool = True) -> str:
        """Compare this set's outputs with the goldens written from the DEFAULT-session
        reference (bench/o54_bench.py goldens). Exact equality: the outputs are rounded
        (4 dp models, 6 dp kw15 / PCA / XGB), so a thread count that changed one digit
        fails here instead of quietly moving the gate."""
        if not Path(path).is_file():
            if required:
                raise RuntimeError(f"goldens file missing: {path} — run "
                                   f"`bench/o54_bench.py goldens` first")
            return "skipped (no goldens file)"
        g = json.loads(Path(path).read_text(encoding='utf-8'))
        mine = self.compute_goldens(g['probes'], g['body'])
        bad = []
        for kind, got in mine.items():
            want = g['outputs'].get(kind)
            if want is None:
                continue
            if kind == 'xgb':
                for c in XGB_COLS:
                    if got.get(c) != want.get(c):
                        bad.append(f"xgb.{c}: {got.get(c)} != {want.get(c)}")
                continue
            for i, (a, b) in enumerate(zip(got, want)):
                if a != b:
                    bad.append(f"{kind}[{i}]: {str(a)[:120]} != {str(b)[:120]}")
        if bad:
            raise RuntimeError(f"goldens self-check FAILED ({len(bad)} mismatch(es); "
                               f"intra_op={self.intra_op}): " + ' | '.join(bad[:6]))
        return f"ok ({', '.join(sorted(mine))})"

    def describe_xgb(self) -> list:
        return [(c, p.get('n_rounds'), r[1], g, k) for c, _b, p, k, r, g in self.xgb_models]


# ─── Engines ──────────────────────────────────────────────────────────────────

class _PriorityPool:
    """N threads serving ONE priority queue (lower first, FIFO within a priority)."""

    def __init__(self, name: str, workers: int, runner):
        self._q = queue.PriorityQueue()
        self._seq = itertools.count()
        self._runner = runner
        self._threads = [threading.Thread(target=self._run, name=f"{name}-{i}", daemon=True)
                         for i in range(max(1, int(workers)))]
        for t in self._threads:
            t.start()

    def submit(self, prio: int, kind: str, args: tuple) -> Future:
        f = Future()
        self._q.put((int(prio), next(self._seq), f, kind, args))
        return f

    def _run(self):
        while True:
            _p, _s, f, kind, args = self._q.get()
            if f is None:
                return
            if not f.set_running_or_notify_cancel():
                continue
            try:
                f.set_result(self._runner(kind, *args))
            except BaseException as exc:          # noqa: BLE001 — mirrors Executor
                f.set_exception(exc)

    def qsize(self) -> int:
        return self._q.qsize()

    def shutdown(self, wait: bool = True, timeout: float = 30.0):
        for _ in self._threads:
            self._q.put((PRIO_SHUTDOWN, next(self._seq), None, None, ()))
        if wait:
            for t in self._threads:
                t.join(timeout)


class ThreadEngine:
    """Every task in THIS process, on priority-queued threads.

    pools='shared'    one pool of `workers` threads for prosus/fmb/flang/embed/xgb + a
                      single kw15 thread. With intra_op=1 each ONNX call runs entirely
                      on its calling thread (ORT releases the GIL), so N threads are N
                      concurrent inferences on N CPUs.
    pools='per-model' 5.3's arrangement: prosus/fmb/flang/xgb pools of `workers` each,
                      kw15 and embed one thread each (embed = 5.3's single leg worker).
    """
    kind = 'threads'

    def __init__(self, pools='shared', workers=6, intra_op=1, spinning=False,
                 goldens_required=True):
        if pools not in ('shared', 'per-model'):
            raise ValueError(f"pools must be 'shared' or 'per-model', not {pools!r}")
        self.pools = pools
        self.workers = int(workers)
        self.intra_op = int(intra_op) if intra_op else None
        self.spinning = bool(spinning)
        self.goldens_required = goldens_required
        self.models = None
        self._route = {}
        self.goldens = None

    def start(self):
        self.models = ModelSet(self.intra_op, self.spinning).load()
        self.goldens = self.models.self_check(required=self.goldens_required)
        run = self.models.run
        if self.pools == 'shared':
            main = _PriorityPool('eng', self.workers, run)
            kw = _PriorityPool('kw15', 1, run)
            self._route = {k: main for k in ('prosus', 'fmb', 'flang', 'embed', 'xgb')}
            self._route['kw15'] = kw
            self._pools = [main, kw]
        else:
            p = {k: _PriorityPool(k, self.workers, run) for k in ('prosus', 'fmb', 'flang', 'xgb')}
            p['kw15'] = _PriorityPool('kw15', 1, run)
            p['embed'] = _PriorityPool('embed', 1, run)
            self._route = p
            self._pools = list(p.values())
        return self

    def submit(self, prio: int, kind: str, *args) -> Future:
        return self._route[kind].submit(prio, kind, args)

    def run_inline(self, kind: str, *args):
        """Run a task on the CALLING thread (checks and benchmarks only)."""
        return self.models.run(kind, *args)

    def qdepth(self) -> int:
        return sum(p.qsize() for p in self._pools)

    def describe(self) -> str:
        return (f"threads/{self.pools} workers={self.workers} "
                f"intra_op={self.intra_op or 'ORT-default'} "
                f"spinning={'on' if (self.spinning or not self.intra_op) else 'off'} "
                f"load={self.models.load_s if self.models else '-'}s goldens={self.goldens}")

    def shutdown(self, wait: bool = True):
        for p in getattr(self, '_pools', []):
            p.shutdown(wait=wait)


class WorkerDied(RuntimeError):
    """The worker process exited, broke its pipe, or hung past WORKER_TASK_TIMEOUT_S."""


class _WorkerProc:
    """One scoring subprocess and the parent's end of its pipe."""

    _tid = itertools.count(1)

    def __init__(self, wid: int, cpus: tuple, intra_op, spinning: bool,
                 goldens_required: bool, logger: logging.Logger):
        self.wid = wid
        self.cpus = tuple(cpus)
        self.intra_op = intra_op
        self.spinning = spinning
        self.goldens_required = goldens_required
        self.log = logger
        self.proc = None
        self.conn = None
        self.info = {}
        self.spawns = 0

    def spawn(self, timeout: float = WORKER_READY_TIMEOUT_S):
        """launch() + handshake() on the CALLING thread — see launch()."""
        self.launch()
        return self.handshake(timeout)

    def launch(self):
        """Start the subprocess; do NOT wait for it.

        ⚠ Call this from a thread that outlives the worker (the main thread, or the
        worker's own feeder thread). The worker asks for PR_SET_PDEATHSIG, and Linux
        delivers that signal when the THREAD that forked the child exits — not the
        process. Launching from a short-lived helper thread got every worker SIGTERMed
        the moment its helper returned (seen 2026-09-24: one silent death per worker,
        no traceback, on its first task)."""
        from multiprocessing.connection import Connection
        parent_sock, child_sock = socket.socketpair()
        child_fd = child_sock.fileno()
        env = dict(os.environ)
        env.update(OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1',
                   TOKENIZERS_PARALLELISM='false')
        cmd = [sys.executable, '-u', str(Path(__file__).resolve()), '--worker',
               '--wid', str(self.wid), '--fd', str(child_fd), '--ppid', str(os.getpid()),
               '--cpus', ','.join(str(c) for c in self.cpus),
               '--intra-op', str(self.intra_op or 0)]
        if self.spinning:
            cmd.append('--spinning')
        if not self.goldens_required:
            cmd.append('--no-goldens')
        self.proc = subprocess.Popen(cmd, pass_fds=(child_fd,), stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                     env=env, close_fds=True)
        child_sock.close()
        self.conn = Connection(parent_sock.detach())
        threading.Thread(target=self._relay_stderr, args=(self.proc,),
                         name=f"w{self.wid}-stderr", daemon=True).start()
        return self

    def handshake(self, timeout: float = WORKER_READY_TIMEOUT_S):
        """Wait for the worker's 'ready' (models loaded, goldens checked)."""
        if not self.conn.poll(timeout):
            self.kill()
            raise RuntimeError(f"worker {self.wid} not ready after {timeout:.0f}s")
        try:
            msg = self.conn.recv()
        except (EOFError, OSError) as exc:
            self.kill()
            raise RuntimeError(f"worker {self.wid} died during startup ({exc}) — see "
                               f"the [W{self.wid}] lines above") from None
        if msg[0] != 'ready':
            self.kill()
            raise RuntimeError(f"worker {self.wid} failed to start:\n{msg[1]}")
        self.info = msg[1]
        self.spawns += 1
        return self

    def _relay_stderr(self, proc):
        try:
            for line in iter(proc.stderr.readline, b''):
                self.log.info("[W%d] %s", self.wid, line.decode('utf-8', 'replace').rstrip())
        except Exception:
            pass

    def call(self, kind: str, args: tuple, timeout: float):
        tid = next(self._tid)
        try:
            self.conn.send(('task', tid, kind, args))
            if not self.conn.poll(timeout):
                raise WorkerDied(f"worker {self.wid}: {kind} task exceeded {timeout:.0f}s "
                                 f"— treating the worker as hung")
            tag, rtid, payload = self.conn.recv()
        except WorkerDied:
            raise
        except (EOFError, OSError, BrokenPipeError, ValueError) as exc:
            raise WorkerDied(f"worker {self.wid} pipe broken: {exc!r}") from None
        if rtid != tid:
            raise WorkerDied(f"worker {self.wid}: reply for task {rtid}, expected {tid}")
        if tag == 'ok':
            return payload
        raise RuntimeError(f"worker {self.wid} {kind}: {payload}")

    def kill(self):
        try:
            if self.conn is not None:
                self.conn.close()
        except Exception:
            pass
        self.conn = None
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass

    def stop(self):
        try:
            if self.conn is not None:
                self.conn.send(('stop',))
        except Exception:
            pass
        if self.proc is not None:
            try:
                self.proc.wait(10)
            except Exception:
                pass
        self.kill()


class ProcessEngine:
    """Every model task in pinned worker subprocesses; this process only dispatches.

    Each worker holds a full ModelSet (the three headliners, kw15, the INT8 embedder +
    PCA, the ten boosters) — ~1.2-1.5 GB RSS each — so a lone PR's four model calls land
    on four different CPUs at once and a burst never waits on this process's GIL."""
    kind = 'procs'

    def __init__(self, workers=6, intra_op=1, cpus=DEFAULT_INFER_CPUS, spinning=False,
                 goldens_required=True, logger=None, task_timeout=WORKER_TASK_TIMEOUT_S):
        self.workers = int(workers)
        self.intra_op = int(intra_op) if intra_op else None
        self.cpus = parse_cpus(cpus)
        self.spinning = bool(spinning)
        self.goldens_required = goldens_required
        self.log = logger or log
        self.task_timeout = float(task_timeout)
        self.cpu_sets = plan_worker_cpus(self.workers, self.cpus)
        self._q = queue.PriorityQueue()
        self._seq = itertools.count()
        self._stopping = False
        self._procs = []
        self._feeders = []
        self.respawns = 0

    def start(self):
        self._procs = [_WorkerProc(i, cs, self.intra_op, self.spinning,
                                   self.goldens_required, self.log)
                       for i, cs in enumerate(self.cpu_sets)]
        errors = []
        # Launch every worker from THIS thread (see _WorkerProc.launch: PDEATHSIG is
        # per-forking-thread), then collect the handshakes; they load in parallel.
        for w in self._procs:
            try:
                w.launch()
            except Exception as exc:       # noqa: BLE001
                errors.append(f"W{w.wid}: {exc}")
        deadline = time.monotonic() + WORKER_READY_TIMEOUT_S
        for w in self._procs:
            if w.conn is None:
                continue
            try:
                w.handshake(max(1.0, deadline - time.monotonic()))
            except Exception as exc:       # noqa: BLE001
                errors.append(f"W{w.wid}: {exc}")
        if errors:
            for w in self._procs:
                w.kill()
            raise RuntimeError("ProcessEngine could not start: " + " | ".join(errors))
        self._feeders = [threading.Thread(target=self._feed, args=(w,),
                                          name=f"feeder-w{w.wid}", daemon=True)
                         for w in self._procs]
        for t in self._feeders:
            t.start()
        return self

    def submit(self, prio: int, kind: str, *args) -> Future:
        f = Future()
        self._q.put((int(prio), next(self._seq), f, kind, args))
        return f

    def _feed(self, w: _WorkerProc):
        while True:
            _p, _s, f, kind, args = self._q.get()
            if f is None:
                return
            if not f.set_running_or_notify_cancel():
                continue
            try:
                f.set_result(w.call(kind, args, self.task_timeout))
            except WorkerDied as exc:
                f.set_exception(exc)
                self.log.error("[Engine] %s — respawning worker %d", exc, w.wid)
                self._respawn(w)
            except BaseException as exc:          # noqa: BLE001
                f.set_exception(exc)

    def _respawn(self, w: _WorkerProc):
        w.kill()
        delays = (1, 5, 30, 60)
        attempt = 0
        while not self._stopping:
            try:
                w.spawn()
                self.respawns += 1
                self.log.warning("[Engine] worker %d respawned (pid %s, CPUs %s)", w.wid,
                                 w.info.get('pid'), w.info.get('cpus'))
                return
            except Exception as exc:              # noqa: BLE001
                d = delays[min(attempt, len(delays) - 1)]
                self.log.error("[Engine] worker %d respawn failed (%s) — retry in %ds",
                               w.wid, exc, d)
                attempt += 1
                time.sleep(d)

    def qdepth(self) -> int:
        return self._q.qsize()

    def worker_pids(self) -> list:
        return [w.proc.pid for w in self._procs if w.proc is not None]

    def describe(self) -> str:
        loads = [w.info.get('load_s') for w in self._procs]
        gold = sorted({str(w.info.get('goldens')) for w in self._procs})
        return (f"procs workers={self.workers} intra_op={self.intra_op or 'ORT-default'} "
                f"cpu_sets={[list(c) for c in self.cpu_sets]} "
                f"spinning={'on' if (self.spinning or not self.intra_op) else 'off'} "
                f"load={loads}s goldens={gold}")

    def shutdown(self, wait: bool = True):
        self._stopping = True
        for _ in self._feeders:
            self._q.put((PRIO_SHUTDOWN, next(self._seq), None, None, ()))
        if wait:
            for t in self._feeders:
                t.join(60)
        for w in self._procs:
            w.stop()


def make_engine(kind='threads', pools='shared', workers=6, intra_op=1, cpus=None,
                spinning=False, goldens_required=True, logger=None):
    if kind == 'threads':
        return ThreadEngine(pools=pools, workers=workers, intra_op=intra_op,
                            spinning=spinning, goldens_required=goldens_required)
    if kind == 'procs':
        return ProcessEngine(workers=workers, intra_op=intra_op, cpus=parse_cpus(cpus),
                             spinning=spinning, goldens_required=goldens_required,
                             logger=logger)
    raise ValueError(f"engine kind must be 'threads' or 'procs', not {kind!r}")


def pin_process(cpus) -> set:
    """Pin EVERY thread of this process (the main thread and any a library already
    started) to `cpus`; threads created later inherit the main thread's mask."""
    want = set(parse_cpus(cpus)) & set(range(os.cpu_count() or 1))
    if not want:
        raise RuntimeError(f"no usable CPU in {cpus!r}")
    os.sched_setaffinity(0, want)
    try:
        for tid in os.listdir('/proc/self/task'):
            try:
                os.sched_setaffinity(int(tid), want)
            except OSError:
                pass
    except OSError:
        pass
    return want


# ─── Worker subprocess entry point ───────────────────────────────────────────

def _set_pdeathsig():
    """Ask the kernel to SIGTERM this worker if its parent dies (Linux only)."""
    try:
        import ctypes
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl(1, signal.SIGTERM)          # PR_SET_PDEATHSIG = 1
    except Exception:
        pass


def _worker_main(argv) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--worker', action='store_true')
    ap.add_argument('--wid', type=int, required=True)
    ap.add_argument('--fd', type=int, required=True)
    ap.add_argument('--ppid', type=int, required=True)
    ap.add_argument('--cpus', required=True)
    ap.add_argument('--intra-op', type=int, default=1)
    ap.add_argument('--spinning', action='store_true')
    ap.add_argument('--no-goldens', action='store_true')
    a = ap.parse_args(argv)

    _set_pdeathsig()
    if os.getppid() != a.ppid:                  # parent died before prctl took effect
        return 0
    # Ctrl+C in the orchestrator's terminal reaches the whole process group; the worker
    # must NOT die on it — the parent stops it cleanly (or PDEATHSIG does).
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    os.sched_setaffinity(0, set(parse_cpus(a.cpus)))

    logging.basicConfig(stream=sys.stderr, level=logging.WARNING,
                        format='%(levelname)s %(name)s: %(message)s')
    wlog = logging.getLogger('o54.worker')
    wlog.setLevel(logging.INFO)

    from multiprocessing.connection import Connection
    conn = Connection(a.fd)
    try:
        ms = ModelSet(intra_op=a.intra_op or None, spinning=a.spinning).load()
        gold = ms.self_check(required=not a.no_goldens)
        try:
            import torch
            torch.set_num_threads(1)
        except Exception:
            pass
        info = {'pid': os.getpid(), 'cpus': sorted(os.sched_getaffinity(0)),
                'load_s': ms.load_s, 'goldens': gold, 'intra_op': ms.intra_op}
        wlog.info("worker %d ready: pid %d CPUs %s intra_op=%s load %.1fs goldens %s",
                  a.wid, info['pid'], info['cpus'], ms.intra_op, ms.load_s, gold)
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
            res = ms.run(kind, *args)
        except Exception as exc:                  # noqa: BLE001
            conn.send(('exc', tid, f"{type(exc).__name__}: {exc}"))
            continue
        conn.send(('ok', tid, res))


def _finite(v) -> bool:
    return isinstance(v, float) and math.isfinite(v)


if __name__ == '__main__':
    if '--worker' in sys.argv:
        sys.exit(_worker_main(sys.argv[1:]))
    print(__doc__)
