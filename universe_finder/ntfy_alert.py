#!/usr/bin/env python3
"""
ntfy_alert.py
-------------
Phone alerts for the nightly universe pipeline, via ntfy.sh.

Reuses the IB Gateway monitor's topic: NTFY_SERVER / NTFY_TOPIC are read from
/home/tom/IBC/.secrets/ntfy.env (override with the NTFY_SECRETS_FILE env var),
the same file `send_ntfy` in /home/tom/IBC/live/scripts/systemd_env_setup.sh
sources. The topic is the only secret — it is never printed or logged.

send_alert() never raises: a failed push must never break the pipeline. Every
alert is also appended to runs/alerts.log, so there is a record on disk even
when the push fails.

Used by daily_universe_pipeline-3.py (crash / run blocked / flawed file) and
universe_watchdog.py (no file by 06:00 ET).

CLI:
    python ntfy_alert.py --test      # send one test message
"""

import os
from datetime import datetime
from pathlib import Path

_SCRIPT_DIR   = Path(__file__).parent
_ALERTS_LOG   = _SCRIPT_DIR / "runs" / "alerts.log"
_SECRETS_FILE = Path(os.environ.get("NTFY_SECRETS_FILE",
                                    "/home/tom/IBC/.secrets/ntfy.env"))


def _read_secrets() -> dict[str, str]:
    """Parse the KEY=value lines of the ntfy secrets file ({} if unreadable)."""
    out: dict[str, str] = {}
    try:
        for line in _SECRETS_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            out[key.strip()] = val.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def _log(status: str, title: str, message: str) -> None:
    try:
        _ALERTS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(_ALERTS_LOG, "a") as fh:
            one_line = message.replace("\n", " | ")
            fh.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {status}: {title} — {one_line}\n")
    except OSError:
        pass


def send_alert(title: str, message: str, priority: str = "high",
               tags: str = "warning", dry_run: bool = False) -> bool:
    """Push one alert to the phone. Returns True if ntfy accepted it."""
    if dry_run:
        print(f"[dry-run] {title}\n{message}")
        return False
    status = "NOT SENT (no ntfy config)"
    sent = False
    try:
        cfg = _read_secrets()
        topic = cfg.get("NTFY_TOPIC")
        if topic:
            import requests
            server = cfg.get("NTFY_SERVER") or "https://ntfy.sh"
            # HTTP headers must be latin-1, so the title is forced to ASCII;
            # the message body is UTF-8 and may hold anything.
            resp = requests.post(
                f"{server.rstrip('/')}/{topic}",
                data=message.encode("utf-8"),
                headers={"Title": title.encode("ascii", "replace").decode(),
                         "Priority": priority, "Tags": tags},
                timeout=8,
            )
            sent = resp.ok
            status = "SENT" if sent else f"NOT SENT (HTTP {resp.status_code})"
    except Exception as exc:  # never let an alert break the caller
        status = f"NOT SENT ({type(exc).__name__})"
    _log(status, title, message)
    return sent


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Send a universe-pipeline ntfy alert")
    ap.add_argument("--test", action="store_true", help="send one test message")
    args = ap.parse_args()
    if args.test:
        ok = send_alert("Universe: test alert",
                        "Test from universe_finder/ntfy_alert.py — alerts reach your phone.",
                        priority="default", tags="white_check_mark")
        print("sent" if ok else f"NOT sent — see {_ALERTS_LOG}")
        raise SystemExit(0 if ok else 1)
    ap.print_help()
