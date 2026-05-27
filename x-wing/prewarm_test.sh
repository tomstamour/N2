#!/usr/bin/env bash
# Manual prewarm test for X-wing-1.0.py (paper Gateway on 4002).
#
# Launches X-wing in --prewarm mode (connects, resolves contract, streams quotes
# but does NOT trade yet), waits, then simulates the orchestrator's decision:
#   fire    -> SIGUSR1  -> X-wing places the BUY LMT at min(ask*(1+pct), cap)
#   abort   -> SIGTERM  -> X-wing disconnects without trading
#   timeout -> (no signal) -> X-wing auto-aborts after --prewarm-timeout
#
# Usage: ./prewarm_test.sh SYMBOL [fire|abort|timeout] [delay_seconds]
#   e.g. ./prewarm_test.sh AAPL fire 4
set -euo pipefail

PY=/home/tom/venv/bin/python
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

SYMBOL="${1:?usage: $0 SYMBOL [fire|abort|timeout] [delay_seconds]}"
ACTION="${2:-fire}"
DELAY="${3:-4}"

"$PY" X-wing-1.0.py \
  --symbol "$SYMBOL" \
  --prewarm --prewarm-timeout 120 \
  --max-limit-entry-percent-price 10 \
  --last-close-price 5 --max-cap-entry-percent 15 \
  --capital 100 \
  --input-limits-table example-yield_vs-stopLimits.tsv \
  --log-dir logs --price-action-table xwing_tables \
  --lifetime 03:00 --port 4002 --client-id-base 11000 &
PID=$!
echo "launched X-wing prewarm pid=$PID symbol=$SYMBOL; ${DELAY}s until '$ACTION'"
sleep "$DELAY"

case "$ACTION" in
  fire)    echo "-> sending SIGUSR1 (fire)";  kill -USR1 "$PID" ;;
  abort)   echo "-> sending SIGTERM (abort)"; kill -TERM  "$PID" ;;
  timeout) echo "-> sending nothing; waiting for --prewarm-timeout auto-abort" ;;
  *)       echo "unknown action '$ACTION' (use fire|abort|timeout)"; kill -TERM "$PID" ;;
esac

wait "$PID"
echo "X-wing exited (pid=$PID)"
