#!/usr/bin/env bash
# view_table.sh - Open a GusTradeTable in VisiData with automatic reloading.
#
# The Gainers Scanner rewrites GusTradeTable.txt each scan cycle. VisiData's
# auto-reload is an *in-app command* (not a CLI flag in v3.0.2), so this wrapper
# opens the file and drives the keystrokes for you via tmux:
#   Space -> reload-modified        (default: reload whenever the file changes)
#   Space -> reload-every -> N       (if a numeric interval is given)
#
# Usage:
#   ./view_table.sh <path-to-GusTradeTable.txt> [interval-seconds]
#
# Examples:
#   ./view_table.sh ./runs/06-Jul-2026/GusTradeTable.txt        # reload on change
#   ./view_table.sh ./runs/06-Jul-2026/GusTradeTable.txt 30     # reload every 30s
set -euo pipefail

TABLE="${1:-}"
INTERVAL="${2:-}"   # empty = reload-modified; a number = reload-every N seconds

if [[ -z "$TABLE" ]]; then
    echo "Usage: $0 <path-to-GusTradeTable.txt> [interval-seconds]" >&2
    exit 1
fi
if [[ ! -f "$TABLE" ]]; then
    echo "Error: file not found: $TABLE" >&2
    echo "(The scanner creates it on its first scan cycle — start the scanner first.)" >&2
    exit 1
fi
if ! command -v vd >/dev/null 2>&1; then
    echo "Error: VisiData ('vd') not found. Install with: pip install visidata" >&2
    exit 1
fi

# Without tmux we cannot auto-send the keystrokes; open plain vd and instruct.
if ! command -v tmux >/dev/null 2>&1; then
    echo "Note: tmux not found — opening VisiData without auto-refresh." >&2
    echo "      Inside VisiData, press Space then type: reload-modified" >&2
    exec vd -f tsv "$TABLE"
fi

SESSION="gttvd_$$"
COLS="$(tput cols 2>/dev/null || echo 200)"
LINES="$(tput lines 2>/dev/null || echo 50)"

cleanup() { tmux kill-session -t "$SESSION" 2>/dev/null || true; }
trap cleanup EXIT

tmux new-session -d -s "$SESSION" -x "$COLS" -y "$LINES" "vd -f tsv '$TABLE'"
sleep 1.5                                   # let the sheet finish loading
tmux send-keys -t "$SESSION" Space          # open the command palette
sleep 0.3
if [[ "$INTERVAL" =~ ^[0-9]+$ ]]; then
    tmux send-keys -t "$SESSION" "reload-every" Enter
    sleep 0.2
    tmux send-keys -t "$SESSION" "$INTERVAL" Enter
else
    tmux send-keys -t "$SESSION" "reload-modified" Enter
fi

# Hand the live session to the user (auto-reload keeps running in-session).
tmux attach-session -t "$SESSION"
