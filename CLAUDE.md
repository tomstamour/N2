# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

N2 — an event-driven news→sentiment→trade pipeline for small-cap stocks on IBKR,
plus the nightly data pipelines and research tools that feed it. A Python monorepo
of standalone processes that talk over loopback TCP; no build system, no test
suite — verify by running a script and inspecting its `logs/`/`tables/` output.

**Most subdirectories have their own CLAUDE.md — always read it before working
there. This file is only the cross-cutting rules.**

## Rules

- **Interpreter**: run everything with the venv python, never system `python3`
  (it lacks `ibapi`, `transformers`, sklearn…). On this machine:
  `/home/tom/venv/bin/python`. Fresh clone: `bash setup.sh` builds `.venv/`
  (see `SETUP.md`). Never commit a venv.
- **Config**: all per-user values (RTPR key, IBKR account/host/port) live in
  `config/n2_config_file.txt`, parsed by `config/n2_config.py`.
- **IBKR ports**: **4001 = LIVE Gateway (real money)**, 4002 = paper GW,
  7497/7496 = paper/live TWS. Run bulk historical-data jobs on the idle paper
  GW (4002) — the live GW drops them under contention.
- **clientIds**: Gateway API slots are limited; clerk pre-claims 1000–1029,
  dailyStats 77, daily-board 20000. Saturation shows up as handshake timeouts.
- **Live chain** (three separate processes):
  `RTPR_connector3` →tcp 8900→ `orchestrator5/Orchestrator5.4.py` →tcp 8765→
  `x-wing-mole/clerk-1.2.py` → Gateway. Run exactly ONE connector variant and
  ONE orchestrator (never two O5.x together). O5.4 pins itself to CPUs 1-3,5-7:
  physical core 0 is left to the clerk, the Gateways and the connector.
- **Git**: only source is tracked (`.py .sh .txt .md`); data/model/log outputs
  are gitignored — never force-add them. Model dirs referenced by live code or
  past backtests are frozen: retrain into a new dataset-stamped dir.
