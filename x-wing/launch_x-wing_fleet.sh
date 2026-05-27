#!/bin/bash
#
# launch_x-wing_fleet.sh — start one X-wing-1.0.py instance per symbol.
#
# Reads a tab-separated config (default: ./fleet-config.tsv) with columns:
#   symbol  entry_limit_price  capital  [limits_table]
# and launches each row as a background process, auto-assigning
# --client-id-base = 10000, 10002, 10004, ...  (each instance uses base & base+1).
#
# PIDs are recorded so stop_x-wing_fleet.sh can shut the fleet down gracefully
# (SIGTERM -> the script flattens @ ask and disconnects).
#
# Usage:
#   ./launch_x-wing_fleet.sh [config.tsv]
# Override shared settings via env vars, e.g.:
#   PORT=4001 LIFETIME=06:30:00 LOG_DIR=/tmp/x ./launch_x-wing_fleet.sh my.tsv
#
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
XWING="${SCRIPT_DIR}/X-wing-1.0.py"

# ---- shared settings (override via env) ----
PYTHON="${PYTHON:-/home/tom/venv/bin/python}"
CONFIG="${1:-${SCRIPT_DIR}/fleet-config.tsv}"
LIMITS_TABLE="${LIMITS_TABLE:-${SCRIPT_DIR}/example-yield_vs-stopLimits.tsv}"
LOG_DIR="${LOG_DIR:-${SCRIPT_DIR}/logs}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-4002}"                      # 4002 paper GW / 4001 live GW / 7497 paper TWS / 7496 live TWS
MARKET_DATA_TYPE="${MARKET_DATA_TYPE:-1}" # 1 real-time, 3 delayed
CLIENT_ID_START="${CLIENT_ID_START:-10000}"
LIFETIME="${LIFETIME:-}"                  # mm:ss, optional
SESSION_END="${SESSION_END:-}"            # HH:MM ET, optional
LOGLEVEL="${LOGLEVEL:-INFO}"

PIDFILE="${SCRIPT_DIR}/x-wing-fleet.pids"

# ---- sanity checks ----
[ -f "$XWING" ]        || { echo "ERROR: not found: $XWING"; exit 1; }
[ -x "$PYTHON" ]       || { echo "ERROR: python not executable: $PYTHON"; exit 1; }
[ -f "$CONFIG" ]       || { echo "ERROR: config not found: $CONFIG (pass one as arg 1)"; exit 1; }
[ -f "$LIMITS_TABLE" ] || { echo "ERROR: limits table not found: $LIMITS_TABLE"; exit 1; }
mkdir -p "$LOG_DIR"

if [ -s "$PIDFILE" ]; then
    echo "WARNING: $PIDFILE already exists and is non-empty."
    echo "         A fleet may already be running. Run ./stop_x-wing_fleet.sh first,"
    echo "         or delete the file if those processes are gone."
    exit 1
fi

if [ "$PORT" = "4001" ] || [ "$PORT" = "7496" ]; then
    echo "############################################################"
    echo "# PORT $PORT is a LIVE trading port. REAL MONEY AT RISK.    #"
    echo "############################################################"
    read -r -p "Type 'LIVE' to continue: " confirm
    [ "$confirm" = "LIVE" ] || { echo "Aborted."; exit 1; }
fi

echo "Launching X-wing fleet"
echo "  config        : $CONFIG"
echo "  python        : $PYTHON"
echo "  limits table  : $LIMITS_TABLE"
echo "  log dir       : $LOG_DIR"
echo "  host:port     : $HOST:$PORT  (mkt-data-type=$MARKET_DATA_TYPE)"
[ -n "$LIFETIME" ]    && echo "  lifetime      : $LIFETIME"
[ -n "$SESSION_END" ] && echo "  session-end   : $SESSION_END ET"
echo

: > "$PIDFILE"
idx=0
launched=0

while IFS=$'\t' read -r symbol entry capital table rest; do
    # strip CR and surrounding whitespace
    symbol="$(echo "${symbol:-}" | tr -d '\r' | xargs)"
    entry="$(echo "${entry:-}" | tr -d '\r' | xargs)"
    capital="$(echo "${capital:-}" | tr -d '\r' | xargs)"
    table="$(echo "${table:-}" | tr -d '\r' | xargs)"

    # skip comments / blanks
    case "$symbol" in ''|\#*) continue ;; esac
    if [ -z "$entry" ] || [ -z "$capital" ]; then
        echo "  SKIP $symbol: missing entry_limit_price or capital"
        continue
    fi

    base=$(( CLIENT_ID_START + 2 * idx ))
    idx=$(( idx + 1 ))

    sym_table="${table:-$LIMITS_TABLE}"
    nohup_out="${LOG_DIR}/nohup-x-wing-${symbol}.out"

    args=(
        --symbol "$symbol"
        --Entry-limit-price "$entry"
        --capital "$capital"
        --input-limits-table "$sym_table"
        --log-dir "$LOG_DIR"
        --client-id-base "$base"
        --host "$HOST"
        --port "$PORT"
        --market-data-type "$MARKET_DATA_TYPE"
        --loglevel "$LOGLEVEL"
    )
    [ -n "$LIFETIME" ]    && args+=( --lifetime "$LIFETIME" )
    [ -n "$SESSION_END" ] && args+=( --session-end "$SESSION_END" )

    nohup "$PYTHON" "$XWING" "${args[@]}" > "$nohup_out" 2>&1 &
    pid=$!
    echo "$pid	$symbol	$base" >> "$PIDFILE"
    echo "  started $symbol  pid=$pid  client-id-base=$base  -> $nohup_out"
    launched=$(( launched + 1 ))
done < "$CONFIG"

echo
echo "Launched $launched instance(s). PIDs in: $PIDFILE"
echo "Stop the fleet with: ${SCRIPT_DIR}/stop_x-wing_fleet.sh"
