# N2 pipeline — setup

How to get the N2 news→sentiment→trade pipeline running on a fresh clone.

All file paths in the code resolve **relative to the repo**, and every per-user
value (RTPR key, IBKR account/host/port, clerk host/port) is read from one file,
`config/n2_config_file.txt`. So a clone needs only: a Python environment with the
dependencies, the FinBERT model exported once, and that one config file edited.

> **Do not commit a virtual environment.** A venv bakes in absolute paths and
> OS/CPU/Python-version-specific binaries — it will not work on another machine.
> Each user builds their own (`setup.sh` makes `.venv/`, which is gitignored).
> The scripts don't care where your venv lives; they run under whatever `python`
> you invoke them with (their shebang is `#!/usr/bin/env python3`).

## Prerequisites (not automated)

- **Python 3.12** (the pinned wheels were captured on 3.12.3).
- **IBKR Gateway or TWS** running and reachable on the host/port you put in the
  config. Port `4001` = LIVE Gateway (real money), `4002` = paper Gateway,
  `7497`/`7496` = paper/live TWS.
- An **RTPR.io account + API key**, *and* a filter rule on
  <https://rtpr.io/wire> — the recommended catch-all is `tickers_length gte 1`.
  Without a rule the alerts WebSocket connects but emits nothing.

## Quickstart

```bash
bash setup.sh                       # builds .venv/, installs deps, exports FinBERT, seeds config
nano config/n2_config_file.txt      # set RTPR_API_KEY and IBKR_ACCOUNT (host/port as needed)

# start the clerk (warm IBKR client pool), then the orchestrator:
.venv/bin/python x-wing-mole/clerk-1.1.py --client-qty 5 --port 4002 --listen-port 8765
.venv/bin/python orchestrator3/Orchestrator4.0.py
```

## Manual steps (equivalent of setup.sh)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
python -m spacy download en_core_web_sm        # no-op if the pinned wheel already installed it
python FinBERT/FinBERT-analysis.py --export     # one-time; writes FinBERT/finbert_onnx/
cp config/n2_config_file.txt config/n2_config_file.txt 2>/dev/null || \
  cp config/n2_config_file_example.txt config/n2_config_file.txt
```

`FinBERT-analysis.py` (body) and `FinBERT-headliner.py` (headline) share the same
`FinBERT/finbert_onnx/` model, so the single export above serves both. If the
headliner ever reports "run the one-time export first," run
`python FinBERT/FinBERT-headliner.py --export`.

## Dependencies

`requirements.txt` pins every third-party package the repo imports (whole tree)
to the versions it was tested against. First runs also fetch, on demand:
the FinBERT base model from Hugging Face (during `--export`) and a SEC-EDGAR
ticker map cached under `~/.cache/NerSecDictionary/`.

## Troubleshooting

- **`ibapi` won't install from PyPI.** The IBKR Python API is sometimes only
  distributed with the TWS API download. If `pip install ibapi==9.81.1.post1`
  fails, install IB's `IBJts/source/pythonclient` (`python setup.py install`)
  into the venv, then re-run `pip install -r requirements.txt`.
- **`torch` is huge / no GPU.** Install the CPU-only build, then the rest:
  ```bash
  pip install torch==2.10.0 --index-url https://download.pytorch.org/whl/cpu
  pip install -r requirements.txt
  ```
- **`OSError: [E050] Can't find model 'en_core_web_sm'.`** Run
  `python -m spacy download en_core_web_sm`.
- **FinBERT raises "run the one-time export first."** `FinBERT/finbert_onnx/` is
  missing — run the export step above.
- **Reproducible byte-exact lock.** `requirements.txt` pins direct deps; pip
  resolves compatible transitive versions. For a fully frozen lock, run
  `pip freeze > requirements.lock.txt` after a successful install.
