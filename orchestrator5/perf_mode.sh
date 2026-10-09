#!/usr/bin/env bash
# perf_mode.sh — CPU performance mode for Orchestrator5.4's burst latency.
#
#   sudo ./perf_mode.sh on  [--min-perf N]   platform_profile=performance, EPP=performance
#                                            on every CPU (+ optional intel_pstate
#                                            min_perf_pct floor, e.g. 50)
#   sudo ./perf_mode.sh off                  back to this box's defaults:
#                                            balanced / balance_performance / 8
#        ./perf_mode.sh status               print the current state (no sudo needed)
#
# Why: the decision budget is 400 ms from reception for EVERY PR of a burst
# (operator, 2026-09-24). A burst starts from an idle CPU: the time the cores take to
# ramp up, and the sustained clock the laptop's platform profile allows, both land on
# the first PRs of a burst. bench/o54_bench.py measures the effect (sweep --tag perf);
# Orchestrator5.4 logs the state at startup ([Engine] CPU power: ...).
#
# Scheduling it for trading hours only (box clock is ET), as root:
#   sudo crontab -e
#     55 3 * * 1-5  /home/tom/Documents/ibkr_scripts/N2/scripts/orchestrator5/perf_mode.sh on
#     5 20 * * 1-5  /home/tom/Documents/ibkr_scripts/N2/scripts/orchestrator5/perf_mode.sh off
#
# Works with intel_pstate in ACTIVE mode and the 'powersave' governor (this box): EPP is
# writable there. Under the 'performance' governor EPP is pinned and the writes fail.
set -euo pipefail

PROFILE=/sys/firmware/acpi/platform_profile
MINPERF=/sys/devices/system/cpu/intel_pstate/min_perf_pct
DEFAULT_PROFILE=balanced
DEFAULT_EPP=balance_performance
DEFAULT_MINPERF=8

status() {
    echo "platform_profile : $(cat "$PROFILE" 2>/dev/null || echo n/a)"
    echo -n "EPP              : "
    cat /sys/devices/system/cpu/cpu[0-9]*/cpufreq/energy_performance_preference 2>/dev/null \
        | sort | uniq -c | awk '{printf "%s x%s  ", $2, $1}'; echo
    echo -n "governor         : "
    cat /sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_governor 2>/dev/null \
        | sort | uniq -c | awk '{printf "%s x%s  ", $2, $1}'; echo
    echo "min_perf_pct     : $(cat "$MINPERF" 2>/dev/null || echo n/a)"
    echo "no_turbo         : $(cat /sys/devices/system/cpu/intel_pstate/no_turbo 2>/dev/null || echo n/a)"
}

need_root() {
    if [ "$(id -u)" -ne 0 ]; then
        echo "perf_mode.sh $1: needs root — run with sudo" >&2
        exit 1
    fi
}

set_epp() {
    local val=$1 f
    for f in /sys/devices/system/cpu/cpu[0-9]*/cpufreq/energy_performance_preference; do
        echo "$val" > "$f"
    done
}

case "${1:-status}" in
    on)
        need_root on
        minperf=""
        if [ "${2:-}" = "--min-perf" ]; then minperf="${3:?--min-perf needs a value}"; fi
        echo performance > "$PROFILE"      # profile first: firmware may reset EPP on change
        set_epp performance
        if [ -n "$minperf" ]; then echo "$minperf" > "$MINPERF"; fi
        logger -t perf_mode "ON (min_perf_pct=${minperf:-unchanged})" 2>/dev/null || true
        status
        ;;
    off)
        need_root off
        echo "$DEFAULT_PROFILE" > "$PROFILE"
        set_epp "$DEFAULT_EPP"
        echo "$DEFAULT_MINPERF" > "$MINPERF"
        logger -t perf_mode "OFF" 2>/dev/null || true
        status
        ;;
    status)
        status
        ;;
    *)
        echo "usage: $0 on [--min-perf N] | off | status" >&2
        exit 2
        ;;
esac
