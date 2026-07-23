#!/usr/bin/env bash
# Install (or update) the dailyStats_pipeline.py crontab entry for the current
# user, using this clone's own path — run it once after cloning the repo.
#
# Usage: ./install_dailyStats_cron.sh [/path/to/python]
#
# Interpreter pick order: $1 if given, else $HOME/venv/bin/python if present
# (it must have pandas, ib_insync and pandas_market_calendars), else python3.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ $# -ge 1 ]; then
    PYTHON="$1"
elif [ -x "$HOME/venv/bin/python" ]; then
    PYTHON="$HOME/venv/bin/python"
else
    PYTHON="$(command -v python3)"
fi

if ! "$PYTHON" -c 'import pandas, ib_insync, pandas_market_calendars' 2>/dev/null; then
    echo "WARNING: $PYTHON is missing pandas/ib_insync/pandas_market_calendars —" >&2
    echo "         the cron job will fail until they are installed." >&2
fi

mkdir -p "$SCRIPT_DIR/outputs/logs"

ENTRY="$(sed -e "s|__PYTHON__|$PYTHON|g" -e "s|__SCRIPT_DIR__|$SCRIPT_DIR|g" "$SCRIPT_DIR/dailyStats.cron")"

# Idempotent: strip any previous install (entry + '# dailyStats:' comment
# block), then append the freshly rendered template.
CURRENT="$(crontab -l 2>/dev/null || true)"
CLEANED="$(printf '%s\n' "$CURRENT" | grep -v 'dailyStats_pipeline\.py' | grep -v '^# dailyStats:' || true)"

printf '%s\n%s\n' "$CLEANED" "$ENTRY" | crontab -

echo "Installed. Current dailyStats crontab entry:"
crontab -l | grep 'dailyStats_pipeline\.py'
