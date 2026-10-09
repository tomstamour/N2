#!/usr/bin/env python3
"""
universe_watchdog.py
--------------------
Morning check that last night's daily_universe_pipeline-3.py run finished.

The pipeline alerts on its own when it crashes, is blocked by the run lock, or
writes a flawed file (see ntfy_alert.py). It cannot alert when it HANGS (the
09-30 → 10-03 Step 1 stall), is killed outright, or never starts. This check
covers those: cron runs it at 06:00 ET and it looks at the run that started at
20:30 the evening before (date D = yesterday). It sends a phone alert unless
BOTH are true:

  * data/stocks_universe_D.tsv exists, and
  * runs/<DD-Mon-YYYY of D>/pipeline_daily-3.log has
    "pipeline_daily-3: finished. stocks_universe_D.tsv" (not a --limit test run).

The alert says whether the run is still going (runs/pipeline_daily-3.lock +
/proc) and quotes the log's last line, which names the step it stopped in.
Silent when all is well (one OK line to stdout for runs/watchdog.log).

CLI:
    python universe_watchdog.py                       # check yesterday
    python universe_watchdog.py --date 2026-09-30     # check another day
    python universe_watchdog.py --dry-run            # print, don't send
"""

import argparse
from datetime import date, datetime, timedelta
from pathlib import Path

from ntfy_alert import send_alert

_SCRIPT_DIR = Path(__file__).parent
_DATA_DIR   = _SCRIPT_DIR / "data"
_RUNS_DIR   = _SCRIPT_DIR / "runs"
_LOCK_FILE  = _RUNS_DIR / "pipeline_daily-3.lock"
_FINISHED   = "pipeline_daily-3: finished"


def _running_pipeline() -> str | None:
    """The lock file's 'pid N started …' line if that pid is still a pipeline-3."""
    try:
        holder = _LOCK_FILE.read_text().strip()
        pid = int(holder.split()[1])
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    except (OSError, IndexError, ValueError):
        return None
    return holder if b"daily_universe_pipeline-3" in cmdline else None


def _last_line(path: Path) -> str:
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return "(no log)"
    return lines[-1][:200] if lines else "(empty log)"


def check(day: date) -> list[str]:
    """Return the problems found for the run started on `day` ([] = fine)."""
    tsv = _DATA_DIR / f"stocks_universe_{day:%Y-%m-%d}.tsv"
    log_path = _RUNS_DIR / f"{day:%d-%b-%Y}" / "pipeline_daily-3.log"
    problems = []
    if not tsv.exists():
        problems.append(f"{tsv.name} does not exist")
    try:
        # Match the canonical file name: a --limit test run logs "finished"
        # too, for stocks_universe_D_limitN.tsv, and must not count.
        finished = f"{_FINISHED}. {tsv.name} " in log_path.read_text(errors="replace")
    except OSError:
        finished = False
    if not finished:
        problems.append(f"the run never logged 'finished' "
                        f"(runs/{log_path.parent.name}/pipeline_daily-3.log)")
    if problems:
        running = _running_pipeline()
        if not running:
            problems.append("no pipeline-3 process is running")
        elif f"started {day:%Y-%m-%d}" in running:
            problems.append(f"still running: {running}")
        else:
            problems.append(f"this run is not running; another one is: {running}")
        problems.append(f"last log line: {_last_line(log_path)}")
    return problems


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--date", metavar="YYYY-MM-DD",
                    help="run date to check (default: yesterday)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the alert instead of sending it")
    args = ap.parse_args()
    day = (datetime.strptime(args.date, "%Y-%m-%d").date() if args.date
           else date.today() - timedelta(days=1))

    problems = check(day)
    stamp = f"{datetime.now():%Y-%m-%d %H:%M:%S}"
    if not problems:
        print(f"{stamp} OK: universe run of {day} finished")
        return
    print(f"{stamp} ALERT: universe run of {day}: " + "; ".join(problems))
    send_alert(f"Universe: no file for {day} by {datetime.now():%H:%M}",
               "\n".join(f"- {p}" for p in problems),
               priority="high", dry_run=args.dry_run)


if __name__ == "__main__":
    main()
