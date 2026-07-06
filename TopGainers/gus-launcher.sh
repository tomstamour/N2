#!/bin/bash

# Cap glibc malloc arenas to keep the scanner's virtual-memory footprint low.
# Each thread can otherwise reserve a 64 MB arena; the scanner re-execs itself
# to set this when unset, but exporting it here applies it from the first pass.
export MALLOC_ARENA_MAX=2
export MALLOC_TRIM_THRESHOLD_=131072

# Creation of a directory with date as a name. This directory will host all file relevant to the daily GUS setup.
./scripts_functions/timer.sh --time 05:46:10 \
  --execute mkdir ./runs/$(date +%d-%b-%Y)

# Running the script that will find the stocks that gained x% in a defined period
./scripts_functions/gainers_scanner_scripts/Gainers_Scanner_Script.py \
  --account U22541307 \
  --date-A "2026-07-02" \
  --gap-percentage 20 \
  --client-id 702 \
  --log ./runs/$(date +%d-%b-%Y)/GSS.log \
  --output-dir ./runs/$(date +%d-%b-%Y)/ \
  --no-new-window \
  --gap_larger_then 47 \
  --institution_smaller_then 20 \
  --float_smaller_then 20 \
  --price_smaller_then 10 \
  --borrow_fee_smaller_then 20 \
  --xVolume-smaller-then 12 \
  --cash-to-play 20 \
  --min-price 0
  #--historical-mode

# Live table view: DO NOT run VisiData inline here. The scanner above runs in the
# foreground (--no-new-window) as a continuous loop, so it never returns, and
# `vd` is a blocking interactive TUI. Instead, open a SEPARATE terminal and run:
#     ./view_table.sh ./runs/$(date +%d-%b-%Y)/GusTradeTable.txt        # reload on change
#     ./view_table.sh ./runs/$(date +%d-%b-%Y)/GusTradeTable.txt 30     # reload every 30s

# Printing the GTT at 09:30./scripts_functions/gainers_scanner_scripts/Gainers_Scanner_Script.py
nohup ./scripts_functions/timer.sh --time 09:29:59 \
  --execute cp ./runs/$(date +%d-%b-%Y)/GusTradeTable.txt \
  ./runs/$(date +%d-%b-%Y)/GTT_930_Print_$(date +%d-%b-%Y).txt &

# Running the Gus Trade Table (GTT) Viewer
./scripts_functions/GTTviewer/GTTviewer.py \
  --input-GTT ./runs/$(date +%d-%b-%Y)/GusTradeTable.txt
