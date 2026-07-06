#!/bin/bash

# timer.sh - Execute a script or command at a specified time
# Usage: ./timer.sh --time HH:MM:SS --execute <script.sh|command> [args...]

set -euo pipefail

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to display usage
usage() {
    cat <<EOF
Usage: $0 --time TIME --execute <SCRIPT|COMMAND> [ARGS...]

Execute a script file or command line at a specified time.

Options:
  --time TIME          Time to execute (HH:MM:SS format or 'now')
  --execute TARGET     Script file or command to execute (with optional arguments)
  -h, --help           Show this help message

Examples (Script Files):
  $0 --time 09:30:01 --execute my_script.py
  $0 --time now --execute my_script2.py
  $0 --time 14:30:00 --execute script.sh arg1 arg2

Examples (Command Lines):
  $0 --time now --execute ls -la
  $0 --time 15:00:00 --execute "echo 'Hello World'"
  $0 --time 16:30:00 --execute "ps aux | grep python"
  $0 --time 09:00:00 --execute df -h

Notes:
  - Time format: HH:MM:SS (24-hour format)
  - If the specified time has already passed today, execution happens immediately
  - Automatically detects if target is a file (script mode) or command (command mode)
  - Script mode: Executes files directly with appropriate interpreter
  - Command mode: Uses bash -c to execute arbitrary commands with pipes, redirects, etc.
  - The tool runs in foreground, showing a countdown timer
EOF
    exit 0
}

# Function to print error messages
error() {
    echo -e "${RED}Error: $1${NC}" >&2
    exit 1
}

# Function to print info messages
info() {
    echo -e "${BLUE}$1${NC}"
}

# Function to print success messages
success() {
    echo -e "${GREEN}$1${NC}"
}

# Function to validate time format
validate_time() {
    local time="$1"

    if [[ "$time" == "now" ]]; then
        return 0
    fi

    # Check if time matches HH:MM:SS format
    if [[ ! "$time" =~ ^([0-1][0-9]|2[0-3]):([0-5][0-9]):([0-5][0-9])$ ]]; then
        error "Invalid time format: $time. Expected HH:MM:SS (24-hour format) or 'now'"
    fi

    return 0
}

# Function to calculate seconds until target time
calculate_wait_time() {
    local target_time="$1"

    if [[ "$target_time" == "now" ]]; then
        echo 0
        return 0
    fi

    # Get current time in seconds since midnight
    local current_seconds=$(date +%s)
    local current_midnight=$(date -d "00:00:00" +%s)
    local seconds_since_midnight=$((current_seconds - current_midnight))

    # Convert target time to seconds since midnight
    local hour=$(echo "$target_time" | cut -d: -f1)
    local minute=$(echo "$target_time" | cut -d: -f2)
    local second=$(echo "$target_time" | cut -d: -f3)

    # Remove leading zeros to avoid octal interpretation
    hour=$((10#$hour))
    minute=$((10#$minute))
    second=$((10#$second))

    local target_seconds=$((hour * 3600 + minute * 60 + second))

    # Calculate wait time
    local wait_time=$((target_seconds - seconds_since_midnight))

    # If time has passed, return 0 (execute immediately)
    if [[ $wait_time -lt 0 ]]; then
        echo 0
    else
        echo "$wait_time"
    fi
}

# Function to format seconds as HH:MM:SS
format_time() {
    local total_seconds="$1"
    local hours=$((total_seconds / 3600))
    local minutes=$(((total_seconds % 3600) / 60))
    local seconds=$((total_seconds % 60))
    printf "%02d:%02d:%02d" $hours $minutes $seconds
}

# Function to display countdown
countdown() {
    local wait_seconds="$1"
    local target_time="$2"

    if [[ $wait_seconds -eq 0 ]]; then
        info "Executing immediately..."
        return 0
    fi

    info "Waiting until $target_time ($(format_time $wait_seconds) remaining)..."
    echo ""

    local remaining=$wait_seconds
    while [[ $remaining -gt 0 ]]; do
        printf "\r${YELLOW}Time remaining: %s${NC} " "$(format_time $remaining)"
        sleep 1
        remaining=$((remaining - 1))
    done

    printf "\r${GREEN}Time reached!${NC}                    \n"
}

# Parse command line arguments
TIME=""
EXECUTE_SCRIPT=""
SCRIPT_ARGS=()

while [[ $# -gt 0 ]]; do
    case $1 in
        --time)
            TIME="$2"
            shift 2
            ;;
        --execute)
            EXECUTE_SCRIPT="$2"
            shift 2
            # Collect remaining arguments for the script
            while [[ $# -gt 0 ]]; do
                SCRIPT_ARGS+=("$1")
                shift
            done
            ;;
        -h|--help)
            usage
            ;;
        *)
            error "Unknown option: $1. Use --help for usage information."
            ;;
    esac
done

# Validate required arguments
if [[ -z "$TIME" ]]; then
    error "Missing required argument: --time"
fi

if [[ -z "$EXECUTE_SCRIPT" ]]; then
    error "Missing required argument: --execute"
fi

# Validate time format
validate_time "$TIME"

# Determine if this is a script file or a command line
IS_SCRIPT_FILE=false
if [[ -f "$EXECUTE_SCRIPT" ]]; then
    IS_SCRIPT_FILE=true
    # Validate script is executable
    if [[ ! -x "$EXECUTE_SCRIPT" ]] && [[ ! "$EXECUTE_SCRIPT" =~ \.(py|sh|rb|pl)$ ]]; then
        error "Script is not executable: $EXECUTE_SCRIPT. Run: chmod +x $EXECUTE_SCRIPT"
    fi
fi

# Calculate wait time
WAIT_SECONDS=$(calculate_wait_time "$TIME")

if [[ "$TIME" == "now" ]]; then
    info "Executing script immediately: $EXECUTE_SCRIPT"
elif [[ $WAIT_SECONDS -eq 0 ]]; then
    info "Specified time $TIME has already passed today."
fi

# Display execution info
if [[ "$IS_SCRIPT_FILE" == true ]]; then
    if [[ ${#SCRIPT_ARGS[@]} -gt 0 ]]; then
        info "Mode: Script file execution"
        info "Script: $EXECUTE_SCRIPT ${SCRIPT_ARGS[*]}"
    else
        info "Mode: Script file execution"
        info "Script: $EXECUTE_SCRIPT"
    fi
else
    if [[ ${#SCRIPT_ARGS[@]} -gt 0 ]]; then
        info "Mode: Command line execution"
        info "Command: $EXECUTE_SCRIPT ${SCRIPT_ARGS[*]}"
    else
        info "Mode: Command line execution"
        info "Command: $EXECUTE_SCRIPT"
    fi
fi

# Countdown
if [[ $WAIT_SECONDS -gt 0 ]]; then
    countdown "$WAIT_SECONDS" "$TIME"
fi

# Execute the script
echo ""
success "========================================="
success "Executing: $EXECUTE_SCRIPT"
success "========================================="
echo ""

# Execute with appropriate method based on type
if [[ "$IS_SCRIPT_FILE" == true ]]; then
    # Script file execution (original behavior)
    if [[ "$EXECUTE_SCRIPT" =~ \.py$ ]]; then
        python3 "$EXECUTE_SCRIPT" "${SCRIPT_ARGS[@]}"
    elif [[ -x "$EXECUTE_SCRIPT" ]]; then
        "$EXECUTE_SCRIPT" "${SCRIPT_ARGS[@]}"
    else
        bash "$EXECUTE_SCRIPT" "${SCRIPT_ARGS[@]}"
    fi
else
    # Command line execution (new behavior)
    if [[ ${#SCRIPT_ARGS[@]} -gt 0 ]]; then
        # Concatenate command with arguments
        bash -c "$EXECUTE_SCRIPT ${SCRIPT_ARGS[*]}"
    else
        bash -c "$EXECUTE_SCRIPT"
    fi
fi

exit_code=$?

echo ""
if [[ $exit_code -eq 0 ]]; then
    success "========================================="
    success "Script completed successfully (exit code: $exit_code)"
    success "========================================="
else
    echo -e "${RED}=========================================${NC}"
    echo -e "${RED}Script failed (exit code: $exit_code)${NC}"
    echo -e "${RED}=========================================${NC}"
fi

exit $exit_code
