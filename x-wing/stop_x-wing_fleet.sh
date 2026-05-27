#!/bin/bash
#
# stop_x-wing_fleet.sh — gracefully stop the X-wing fleet started by
# launch_x-wing_fleet.sh.
#
# Sends SIGTERM first so each instance runs its shutdown path (flatten the open
# long with a SELL LMT @ ask, then disconnect both clients). Only processes that
# do not exit within the grace period are SIGKILL'd.
#
# Usage:
#   ./stop_x-wing_fleet.sh           # graceful: SIGTERM, wait, then SIGKILL leftovers
#   ./stop_x-wing_fleet.sh --force   # immediate SIGKILL (skips flatten — use with care)
#
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIDFILE="${SCRIPT_DIR}/x-wing-fleet.pids"
GRACE="${GRACE:-20}"   # seconds to wait for graceful flatten + disconnect

FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1

[ -f "$PIDFILE" ] || { echo "No pidfile ($PIDFILE); nothing to stop."; exit 0; }

pids=()
while IFS=$'\t' read -r pid symbol base; do
    [ -n "${pid:-}" ] || continue
    pids+=("$pid")
    if kill -0 "$pid" 2>/dev/null; then
        if [ "$FORCE" -eq 1 ]; then
            echo "SIGKILL $symbol (pid=$pid)"
            kill -9 "$pid" 2>/dev/null
        else
            echo "SIGTERM $symbol (pid=$pid) — flattening @ ask + disconnecting"
            kill -TERM "$pid" 2>/dev/null
        fi
    else
        echo "already stopped: $symbol (pid=$pid)"
    fi
done < "$PIDFILE"

if [ "$FORCE" -eq 0 ]; then
    echo "Waiting up to ${GRACE}s for graceful shutdown..."
    for _ in $(seq 1 "$GRACE"); do
        alive=0
        for pid in "${pids[@]}"; do
            kill -0 "$pid" 2>/dev/null && alive=$(( alive + 1 ))
        done
        [ "$alive" -eq 0 ] && break
        sleep 1
    done
    for pid in "${pids[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            echo "still alive after ${GRACE}s -> SIGKILL pid=$pid"
            kill -9 "$pid" 2>/dev/null
        fi
    done
fi

rm -f "$PIDFILE"
echo "Fleet stopped. Removed $PIDFILE"
