# Repository Guidelines

## Project Structure & Module Organization

This repository is a Python script collection for building and enriching small-cap stock universes. Top-level `.py` files are the working modules and CLIs. Key entry points include `NASDAQ_symbols_data.py` for symbol/fundamental cache generation, `daily_universe_pipeline-2.py` for the trade-size-enabled pipeline, `ITI_imputer.py`, and `trade_size_imputer.py`. Generated TSVs live in `data/`; runtime logs live in dated folders under `runs/`. Design notes and research references are in `doc/`. Treat `data/`, `runs/`, and `__pycache__/` as generated output unless a task explicitly targets them.

## Build, Test, and Development Commands

- `python3 -m py_compile *.py` checks all top-level scripts for syntax errors.
- `python3 NASDAQ_symbols_data.py` builds or reuses the 24-hour `data/nasdaq_symbols_data.tsv` cache.
- `python3 NASDAQ_symbols_data.py --refresh` forces a fresh symbol/fundamental fetch.
- `python3 ./daily_universe_pipeline-2.py --max-float 50 --limit 100` runs a bounded pipeline sample; it requires a reachable IBKR Gateway/TWS.
- `python3 ITI_imputer.py --date YYYY-MM-DD` imputes a canonical dated pipeline TSV.
- `python3 trade_size_imputer.py --input data/<file>.tsv` imputes trade-size columns in a sized output file.

## Coding Style & Naming Conventions

Use Python 3, four-space indentation, and the existing script-oriented style. Keep constants near the top with uppercase names such as `_DATA_DIR` and `_PIPELINE_SENTINEL`. Prefer `pathlib.Path`, named loggers, explicit CLI arguments via `argparse`, and tab-separated TSV I/O through `pandas`. Preserve existing sentinel semantics: pipeline fetch failures use `44444`, while intentionally skipped rows remain blank or NaN.

## Testing Guidelines

No formal test framework is currently present. Before committing code changes, run `python3 -m py_compile *.py` and exercise the smallest relevant CLI path, using `--limit` for pipeline work. For changes touching file output, inspect headers and row counts in the resulting TSV. For IBKR-dependent paths, document the Gateway/TWS port, client ID, and whether the run was live or limited.

## Commit & Pull Request Guidelines

Recent commits use short, plain-language subjects such as `clerk config file added` and snapshot-style messages. Keep subjects concise and specific. Pull requests should describe the affected script, commands run, generated files changed, and any external dependencies used. Include log excerpts or TSV schema changes when behavior changes.

## Security & Configuration Tips

Do not hard-code credentials or machine-specific absolute paths. Avoid committing generated market data or large logs unless they are required fixtures. Be careful with full pipeline runs: they can make network calls, use IBKR historical data, and overwrite canonical dated TSV outputs.
