# Low-Memory Mode & VisiData Live View

This document describes how the Gainers Scanner keeps its memory footprint small
and how to watch the results table update live in a separate process using
[VisiData](https://www.visidata.org/).

## Background

The scanner does **not** render a live table inside its own process. Each scan
cycle it:

1. prints a few plain status lines to its terminal, and
2. rewrites the `GusTradeTable.txt` file on disk (all tracked stocks).

Because the table already lives on disk, the live "view" is best handled by a
**separate** program that re-reads the file on a timer. That keeps the scanner
process lean — it does zero rendering work.

## Memory optimizations in the scanner

| Change | Why |
| --- | --- |
| `pandas` / `yfinance` imported lazily (only inside the functions that use them) | Avoids loading numpy/requests/lxml/curl_cffi on every run — large RSS/VSZ saving when those code paths aren't hit. |
| `MALLOC_ARENA_MAX=2` set via a one-time re-exec at the top of `main()` | glibc otherwise reserves up to 64 MB of **virtual** address space per malloc arena per thread. Capping arenas is the single biggest lever on VSZ for a threaded Python process. |
| Stock-processing thread pools reduced from 20 → `SCAN_MAX_WORKERS` (8) | Fewer live threads = fewer arenas and less stack reservation. |
| `--refresh-interval` default raised 3s → 30s | Fewer scan cycles, IBKR/yfinance calls, and disk rewrites. |
| Atomic table writes (`.tmp` + `os.replace`) | A reader (VisiData) never sees a half-written file. |

`MALLOC_ARENA_MAX` is applied automatically by the script's re-exec guard, so it
works for both `--no-new-window` runs and the new-terminal-window launch. You can
also export it yourself before launching (e.g. in `gus-launcher.sh`):

```bash
export MALLOC_ARENA_MAX=2
```

### Verifying the memory win

While a scan is running:

```bash
# VSZ/RSS of the scanner process (KB)
ps -o pid,vsz,rss,cmd -p <pid>

# Confirm the arena cap is active in the running process
tr '\0' '\n' < /proc/<pid>/environ | grep MALLOC_ARENA_MAX
```

## Live view with VisiData

VisiData auto-reload is an **in-app command**, not a command-line flag (there is
no `--reload-every` option in VisiData 3.0.2). Open the table, then run the
command:

```bash
vd -f tsv /path/to/runs/DD-MMM-YYYY/GusTradeTable.txt
```

Inside VisiData, press **`Space`** to open the command palette, then run one of:

- **`reload-modified`** — reloads whenever the file changes on disk (tail-like,
  ~1s latency). Recommended; pairs with the scanner's atomic writes.
- **`reload-every`** — then enter a fixed interval in seconds (e.g. `30`).

Notes:
- `-f tsv` forces tab-delimited parsing (the file uses a `.txt` extension).
- Any sort you set in VisiData (e.g. `[` on the `GapL(%)` column) is reapplied on
  each reload, so the live view stays sorted.
- Press `q` / `gq` to quit VisiData; it does not affect the scanner.

### Convenience wrapper

`view_table.sh` (project root) opens the file and drives those keystrokes for you
via tmux, so auto-reload starts automatically:

```bash
./view_table.sh /path/to/runs/DD-MMM-YYYY/GusTradeTable.txt        # reload on change
./view_table.sh /path/to/.../GusTradeTable.txt 30                  # reload every 30s
```

If tmux is not installed the wrapper still opens VisiData, and you run
`Space` → `reload-modified` yourself.
