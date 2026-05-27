#!/usr/bin/env python3
"""
test_main.py — Manual integration test for NewsWatcher3.

Usage:
    python3 test_main.py

Expects:
  - RTPR_API-Key.txt in the current directory (single 'Key:' line)
  - nasdaq_symbols_data.tsv in the current directory
  - black_list.csv and excluded_strings.txt in the current directory

What it verifies:
  1. start() returns immediately
  2. get_news_df() returns a DataFrame (initially empty)
  3. Every POLL_INTERVAL seconds prints the current DataFrame
  4. Double-start raises RuntimeError
  5. Ctrl-C → stop() → final flush → JSON files written to blocked_PRs/ and accepted_PRs/

Ctrl-C to stop after observing behaviour.
"""

import sys
import time
from pathlib import Path

import NewsWatcher3 as nw

POLL_INTERVAL = 30      # seconds between DataFrame prints
RUN_DURATION  = 86400     # max seconds before auto-stop


def my_callback(news_dict):
    print(
        f">>> CALLBACK: {news_dict['Symbol']} | id={news_dict['ID']} | "
        f"{news_dict['Headline'][:80]}"
    )


def main():
    print("=" * 60)
    print("NewsWatcher3 integration test")
    print("=" * 60)

    if not Path("./RTPR_API-Key.txt").exists():
        print("ERROR: ./RTPR_API-Key.txt not found.")
        sys.exit(1)
    if not Path("/home/tom/Documents/ibkr_scripts/N1/scripts/universe_finder/data/nasdaq_symbols_data_priced.tsv").exists():
        print("ERROR: /home/tom/Documents/ibkr_scripts/N1/scripts/universe_finder/data/nasdaq_symbols_data_priced.tsv not found.")
        sys.exit(1)

    nw.register_callback(my_callback)

    nw.start(
        universe_tsv="/home/tom/Documents/ibkr_scripts/N1/scripts/universe_finder/data/nasdaq_symbols_data_priced.tsv",
        black_list="./black_list.csv",
        blacklist_expiry_days=7,
        api_keys="./RTPR_API-Key.txt",
        log_dir="./logs",
        output_dir="./outputs",
        news_df_dir="./outputs",
        blocked_dir="./blocked_PRs",
        accepted_dir="./accepted_PRs",
        excluded_strings_file="./excluded_strings.txt",
        priced_tsv="/home/tom/Documents/ibkr_scripts/N1/scripts/universe_finder/data/nasdaq_symbols_data_priced.tsv",
        reject_float_greater_then=50,
        reject_price_greater_then=2.00,
        flush_interval_seconds=300,
    )
    print("start() returned immediately — background thread is connecting...")

    df = nw.get_news_df()
    print(f"\nInitial DataFrame (should be empty): {len(df)} rows")
    print(df)

    # Double-start guard
    try:
        nw.start(
            universe_tsv="/home/tom/Documents/ibkr_scripts/N1/scripts/universe_finder/data/nasdaq_symbols_data_priced.tsv",
            black_list="./black_list.csv",
            blacklist_expiry_days=7,
            api_keys="./RTPR_API-Key.txt",
        )
        print("ERROR: double-start should have raised RuntimeError")
    except RuntimeError as e:
        print(f"\nDouble-start guard works: {e}")

    print(f"\nPolling every {POLL_INTERVAL}s for up to {RUN_DURATION}s...")
    print("(Press Ctrl-C to stop early)\n")

    elapsed = 0
    last_id = None
    try:
        while elapsed < RUN_DURATION:
            time.sleep(POLL_INTERVAL)
            elapsed += POLL_INTERVAL

            df = nw.get_news_df()
            print(f"\n--- [{elapsed}s elapsed] Accepted DataFrame: {len(df)} rows ---")
            if not df.empty:
                print(df.to_string(index=False))
                last_id = f"id-{df.iloc[-1]['ID']}"
            else:
                print("(no accepted items yet)")

    except KeyboardInterrupt:
        print("\nCtrl-C received — stopping...")

    print("\nCalling stop()...")
    nw.stop()
    print("stop() returned.")

    if last_id:
        print(f"\nTesting get_news_object('{last_id}') after stop + prune...")
        result = nw.get_news_object(last_id)
        if result is None:
            print("Returned None as expected (item was pruned on final flush).")
        else:
            print(f"Got object: {list(result.keys())}")

    print("\nTest complete.")


if __name__ == "__main__":
    main()
