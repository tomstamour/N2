#!/usr/bin/env bash
#
# setup.sh — one-shot environment bootstrap for the N2 pipeline.
#
# Creates a local virtual environment (.venv/), installs all pinned
# dependencies, ensures the spaCy model is present, performs the one-time
# FinBERT ONNX export, and seeds the per-user config file. Safe to re-run.
#
# Usage:
#   bash setup.sh
# then edit config/n2_config_file.txt and launch (see SETUP.md).
#
# A virtual environment is NEVER committed — it is machine/OS/Python-version
# specific. .venv/ is gitignored; this script (re)builds it locally.

set -euo pipefail

# Always operate from the repo root (this script's own directory).
cd "$(dirname "$0")"
REPO_ROOT="$(pwd)"
VENV_DIR="${REPO_ROOT}/.venv"
PY="${VENV_DIR}/bin/python"

echo "==> N2 setup starting in ${REPO_ROOT}"

# 1) Python check ------------------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 not found on PATH. Install Python 3.12, then re-run." >&2
  exit 1
fi
PYVER="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
echo "==> system python3 is ${PYVER} (this repo's wheels were pinned on 3.12)"
if [ "${PYVER}" != "3.12" ]; then
  echo "    WARNING: pinned wheels were captured on Python 3.12; another version"
  echo "             may require different builds (notably torch / onnxruntime)."
fi

# 2) Virtual environment -----------------------------------------------------
if [ ! -d "${VENV_DIR}" ]; then
  echo "==> creating virtual environment at ${VENV_DIR}"
  python3 -m venv "${VENV_DIR}"
else
  echo "==> reusing existing virtual environment at ${VENV_DIR}"
fi

# 3) Dependencies ------------------------------------------------------------
echo "==> upgrading pip and installing requirements.txt (torch is large — this can take a while)"
"${PY}" -m pip install --upgrade pip
"${PY}" -m pip install -r requirements.txt

# 4) spaCy model (no-op if the pinned wheel in requirements.txt already got it)
echo "==> ensuring spaCy en_core_web_sm model is present"
"${PY}" -m spacy download en_core_web_sm || true

# 5) One-time FinBERT ONNX export -------------------------------------------
# FinBERT-analysis.py and FinBERT-headliner.py share one __file__-relative
# model dir (FinBERT/finbert_onnx/), so a single export serves both.
if [ -f "${REPO_ROOT}/FinBERT/finbert_onnx/model.onnx" ]; then
  echo "==> FinBERT ONNX model already present — skipping export"
else
  echo "==> exporting FinBERT ONNX model (one-time; downloads the HF base model)"
  "${PY}" FinBERT/FinBERT-analysis.py --export
fi

# 6) Per-user config ---------------------------------------------------------
CFG="${REPO_ROOT}/config/n2_config_file.txt"
CFG_EXAMPLE="${REPO_ROOT}/config/n2_config_file_example.txt"
if [ ! -f "${CFG}" ]; then
  echo "==> seeding config/n2_config_file.txt from the example template"
  cp "${CFG_EXAMPLE}" "${CFG}"
  echo "    >>> EDIT config/n2_config_file.txt: set RTPR_API_KEY and IBKR_ACCOUNT <<<"
else
  echo "==> config/n2_config_file.txt already exists — leaving it untouched"
fi

cat <<EOF

==> Setup complete.

Next steps:
  1. Edit  config/n2_config_file.txt   (RTPR_API_KEY, IBKR_ACCOUNT, gateway host/port)
  2. Ensure IBKR Gateway/TWS is running, and an RTPR filter rule exists
     (rtpr.io/wire:  tickers_length gte 1).
  3. Start the clerk, then the orchestrator, with the venv's python:
       ${VENV_DIR}/bin/python x-wing-mole/clerk-1.1.py --client-qty 5 --port 4002 --listen-port 8765
       ${VENV_DIR}/bin/python orchestrator3/Orchestrator4.0.py
     (--port 4001 is the LIVE Gateway = real money; 4002 = paper.)

  For interactive use, activate the venv:  source ${VENV_DIR}/bin/activate
EOF
