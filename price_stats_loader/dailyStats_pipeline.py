"""Nightly PR-stats pipeline: grow the unfiltered master TSV and score new rows.

Meant to be fired by cron every evening at 20:05 (see dailyStats.cron /
install_dailyStats_cron.sh); the script itself decides whether there is work,
using the NYSE trading calendar:

  1. A day-file ../orchestrator5/tables/news_output_<D>.tsv becomes eligible
     only once the first trading session AFTER D has fully ended (20:00 ET).
     This guarantees every PR's fromPR_High window is complete before it is
     scored — a PR created after the 16:00 close (or on a weekend/holiday)
     has its window end at 20:00 ET of the NEXT trading day. In practice:
     weekday files land the next evening; Friday + weekend files all land on
     Monday's run; Saturday/Sunday runs are no-ops.
  2. Eligible files not yet consumed (tracked in outputs/.dailyStats_state.json)
     are appended to the current master via enriched_tsv_concatener.py, writing
     a new master named 02_PRstats_<START>-<newEND>_unfiltered.tsv. Old masters
     are kept.
  3. fromPR_HighOfDay_addOn.py --only-missing then scores only the rows that
     have no fromPR_High(%) yet (clientId 77 on the paper Gateway, port 4002).

A failed/aborted step 3 is self-healing: partial results are saved and the
next night's run retries the still-missing rows.
"""

import argparse
import fcntl
import json
import logging
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

# Reuse the trading-calendar helpers and logging setup of the addon (venv-only
# deps: ib_insync, pandas_market_calendars).
from fromPR_HighOfDay_addOn import (  # noqa: E402
    EASTERN, EXTENDED_CLOSE_ET_HOUR, next_trading_day, setup_logging,
)

TABLES_DIR = (SCRIPT_DIR / '..' / 'orchestrator5' / 'tables').resolve()
OUTPUTS_DIR = SCRIPT_DIR / 'outputs'
STATE_PATH = OUTPUTS_DIR / '.dailyStats_state.json'
LOCK_PATH = OUTPUTS_DIR / '.dailyStats.lock'
PIPELINE_LOG = OUTPUTS_DIR / 'logs' / 'dailyStats_pipeline.log'
FROMPR_LOG = OUTPUTS_DIR / 'logs' / 'fromPR_cron.log'

MASTER_RE = re.compile(r'^02_PRstats_(\d{4}-\d{2}-\d{2})-(\d{4}-\d{2}-\d{2})_unfiltered\.tsv$')
DAYFILE_RE = re.compile(r'^news_output_(\d{4}-\d{2}-\d{2})\.tsv$')

# Unique on this Gateway: 6 highOfDay, 7 fromPR manual default, 10 universe
# pipeline (20:30, same paper GW), 1000-1029 clerk, 10000+ x-wing, 20000 board.
PIPELINE_CLIENT_ID = 77


def window_complete(file_date: date, now_et: datetime) -> bool:
    """True once the first trading session after file_date has fully ended."""
    nxt = next_trading_day(file_date)
    session_end = datetime(nxt.year, nxt.month, nxt.day,
                           EXTENDED_CLOSE_ET_HOUR, tzinfo=EASTERN)
    return now_et >= session_end


def list_day_files() -> list[tuple[date, str]]:
    """All news_output_YYYY-MM-DD.tsv in the tables dir, sorted by date."""
    found = []
    for p in TABLES_DIR.iterdir():
        m = DAYFILE_RE.match(p.name)
        if m:
            found.append((date.fromisoformat(m.group(1)), p.name))
    return sorted(found)


def find_master() -> tuple[Path, str, str]:
    """Locate the current master; returns (path, start_str, end_str).

    Prefers the state file; falls back to the newest-END file matching the
    02_PRstats_<START>-<END>_unfiltered.tsv pattern in outputs/.
    """
    if STATE_PATH.is_file():
        state = json.loads(STATE_PATH.read_text(encoding='utf-8'))
        name = state.get('master', '')
        m = MASTER_RE.match(name)
        if m and (OUTPUTS_DIR / name).is_file():
            return OUTPUTS_DIR / name, m.group(1), m.group(2)
        logging.warning("State master %r missing or malformed — falling back to glob.", name)

    candidates = []
    for p in OUTPUTS_DIR.glob('02_PRstats_*_unfiltered.tsv'):
        m = MASTER_RE.match(p.name)
        if m:
            candidates.append((m.group(2), m.group(1), p))
    if not candidates:
        sys.exit(f"Error: no master matching 02_PRstats_<start>-<end>_unfiltered.tsv in {OUTPUTS_DIR}")
    end, start, path = max(candidates)
    return path, start, end


def load_state(master_end: str) -> dict:
    """Load the state file, bootstrapping 'consumed' on the first run.

    Bootstrap rule: every day-file dated on or before the master's END date is
    already part of the master (that is what the seed was built from).
    """
    if STATE_PATH.is_file():
        return json.loads(STATE_PATH.read_text(encoding='utf-8'))
    end_date = date.fromisoformat(master_end)
    consumed = [name for d, name in list_day_files() if d <= end_date]
    logging.info("No state file — bootstrapping: %d day-files dated <= %s marked as consumed.",
                 len(consumed), master_end)
    return {'master': '', 'consumed': consumed}


def save_state(state: dict) -> None:
    tmp = STATE_PATH.with_name(STATE_PATH.name + '.tmp')
    tmp.write_text(json.dumps(state, indent=2) + '\n', encoding='utf-8')
    tmp.replace(STATE_PATH)


def run_step(name: str, cmd: list[str]) -> int:
    logging.info("%s: %s", name, ' '.join(cmd))
    proc = subprocess.run(cmd, cwd=SCRIPT_DIR)
    if proc.returncode != 0:
        logging.error("%s failed (exit code %d).", name, proc.returncode)
    return proc.returncode


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be concatenated/scored, change nothing.')
    args = parser.parse_args()

    PIPELINE_LOG.parent.mkdir(parents=True, exist_ok=True)
    setup_logging(None if args.dry_run else str(PIPELINE_LOG))

    lock_fh = open(LOCK_PATH, 'w')
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        logging.error("Another dailyStats_pipeline run is in progress — exiting.")
        sys.exit(1)

    now_et = datetime.now(EASTERN)
    master_path, start_str, end_str = find_master()
    state = load_state(end_str)
    consumed = set(state.get('consumed', []))

    eligible = [(d, name) for d, name in list_day_files()
                if name not in consumed and window_complete(d, now_et)]

    logging.info("Master: %s | now: %s ET | eligible new day-files: %d",
                 master_path.name, now_et.strftime('%Y-%m-%d %H:%M'), len(eligible))
    for d, name in eligible:
        logging.info("  eligible: %s", name)

    if not eligible:
        logging.info("Nothing eligible to concatenate — done.")
        return

    # Never move the END date backwards (late-arriving old file edge case).
    new_end = max(max(d for d, _ in eligible).isoformat(), end_str)
    new_master = OUTPUTS_DIR / f"02_PRstats_{start_str}-{new_end}_unfiltered.tsv"

    if args.dry_run:
        logging.info("[dry-run] would write %s, then score missing rows "
                     "(clientId %d, port 4002).", new_master.name, PIPELINE_CLIENT_ID)
        return

    # Step 1: concatenate.
    rc = run_step('Step 1 (concat)', [
        sys.executable, str(SCRIPT_DIR / 'enriched_tsv_concatener.py'),
        '--base', str(master_path),
        '--add', *[str(TABLES_DIR / name) for _, name in eligible],
        '-o', str(new_master),
    ])
    if rc != 0:
        sys.exit(1)

    state['master'] = new_master.name
    state['consumed'] = sorted(consumed | {name for _, name in eligible})
    save_state(state)
    logging.info("State updated: master=%s, consumed=%d files.",
                 new_master.name, len(state['consumed']))

    # Step 2: score rows that have no fromPR_High(%) yet, in place.
    rc = run_step('Step 2 (fromPR_High)', [
        sys.executable, str(SCRIPT_DIR / 'fromPR_HighOfDay_addOn.py'),
        '--input', str(new_master),
        '--output', str(new_master),
        '--only-missing',
        '--client-id', str(PIPELINE_CLIENT_ID),
        '--log', str(FROMPR_LOG),
    ])
    if rc != 0:
        # Partial results were saved by the addon; missing rows are retried
        # on the next nightly run.
        sys.exit(rc)

    logging.info("Pipeline complete: %s", new_master.name)


if __name__ == '__main__':
    main()
