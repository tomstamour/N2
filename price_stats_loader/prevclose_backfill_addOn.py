"""Backfill the LastDailyClose (prior trading-day close) column of a PR-stats TSV.

LastDailyClose is the baseline that DailyHigh(%) is computed against
((high - prev_close) / prev_close * 100). In older enriched TSVs it is only
present on the ~20% of rows that came through the universe pipeline; this script
fills the rest by fetching the prior-day close independently from IBKR.

It reuses the proven machinery in highOfDay_addOn.py -- the same trading-calendar
target-date logic, US-exchange gating, historical fetch (which already returns
prev_close), reconnect handling, per-(symbol, date) caching, and HMDS
consecutive-failure abort -- so results line up with how DailyHigh(%) itself was
built. The independent fetch (rather than recovering the close algebraically from
DailyHigh($)/DailyHigh(%)) keeps the column usable as an honest predictor of the
move instead of a value derived from it.

By default only rows with a missing LastDailyClose are fetched; --overwrite
re-fetches every row so the whole column comes from one source.

Run with the ibapi venv:
    /home/tom/venv/bin/python prevclose_backfill_addOn.py \
        -i outputs/PR-stats-...tsv -l /tmp/backfill.log
"""

import argparse
import logging
import os

import pandas as pd

# Reuse the fetch/calendar/connection machinery instead of duplicating it.
from highOfDay_addON import (
    IB_HOST, IB_PORT, CLIENT_ID, PACING_SLEEP_SECONDS, CONSECUTIVE_FAILURE_ABORT,
    setup_logging, is_us_exchange, get_target_date, fetch_hod_and_prev_close,
)
from ib_insync import IB


def build_output_path(input_path: str) -> str:
    root, ext = os.path.splitext(input_path)
    return f"{root}_pricefilled{ext}"


def is_missing(value) -> bool:
    """True if a LastDailyClose cell has no usable numeric value."""
    return pd.isna(pd.to_numeric(value, errors='coerce'))


def main():
    parser = argparse.ArgumentParser(
        description=(
            'Backfill LastDailyClose (prior trading-day close) on a PR-stats TSV '
            'by fetching each symbol\'s prior close from IBKR historical data. '
            'Reuses highOfDay_addOn.py for target-date and fetch logic. Skips '
            'non-US exchanges. Output is written to <input_stem>_pricefilled.tsv.'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--input', '-i', required=True, metavar='FILE',
                        dest='input_file',
                        help='Input TSV (needs Symbol, ArrivalTime, Exchange; '
                             'ArrivalDate if ArrivalTime is time-only).')
    parser.add_argument('--output-dir', '-o', metavar='DIR', dest='output_dir',
                        default=None,
                        help="Directory for the output TSV. Defaults to the "
                             "input file's directory.")
    parser.add_argument('--log', '-l', metavar='FILE', dest='log_file',
                        default=None, help='Optional log file (appended).')
    parser.add_argument('--overwrite', action='store_true',
                        help='Re-fetch LastDailyClose for every row, not just '
                             'rows where it is missing (single-source column).')
    args = parser.parse_args()

    setup_logging(args.log_file)
    if args.log_file:
        logging.info(f"Logging to {os.path.abspath(args.log_file)}")

    if not os.path.isfile(args.input_file):
        logging.error(f"Input file not found: {args.input_file}")
        return

    df = pd.read_csv(args.input_file, sep='\t')
    if 'Symbol' not in df.columns:
        logging.error("Input TSV must have a 'Symbol' column.")
        return
    if 'LastDailyClose' not in df.columns:
        df['LastDailyClose'] = None

    ib = IB()
    try:
        logging.info(f"Connecting to IBKR at {IB_HOST}:{IB_PORT} clientId={CLIENT_ID}...")
        try:
            ib.connect(IB_HOST, IB_PORT, clientId=CLIENT_ID, timeout=20)
        except ConnectionRefusedError:
            logging.error(
                f"Connection refused on {IB_HOST}:{IB_PORT}. "
                "Make sure TWS or IB Gateway is running with API connections enabled."
            )
            return
        except Exception as e:
            logging.error(f"Could not connect to IBKR: {e}")
            return
        if not ib.isConnected():
            logging.error("Failed to connect to IBKR. Exiting.")
            return
        logging.info("Connected.")

        cache: dict = {}
        consecutive_failures = 0
        filled = skipped_present = skipped_exchange = failed = 0

        for idx, row in df.iterrows():
            if not args.overwrite and not is_missing(row['LastDailyClose']):
                skipped_present += 1
                continue

            symbol = str(row['Symbol']).strip()
            arrival_str = str(row['ArrivalTime']).strip()
            arrival_date_str = str(row.get('ArrivalDate', '')).strip()
            exchange = str(row.get('Exchange', '')).strip()

            if exchange and not is_us_exchange(exchange):
                logging.info(f"{symbol}: exchange '{exchange}' not in US subscription — skipping.")
                skipped_exchange += 1
                continue

            try:
                target_dt = get_target_date(arrival_str, arrival_date_str)
            except Exception as e:
                logging.warning(f"Row {idx} ({symbol}): could not parse ArrivalTime '{arrival_str}' — {e}")
                failed += 1
                continue

            cache_key = (symbol, target_dt)
            if cache_key not in cache:
                logging.info(f"Fetching prior close for {symbol} on {target_dt}...")
                _high, prev_close, status = fetch_hod_and_prev_close(ib, symbol, target_dt)
                cache[cache_key] = prev_close

                # 'skip' (bad contract / non-trading target) is a per-symbol data
                # condition and must not trip the HMDS-outage abort; only genuine
                # 'failure' responses count.
                if status == 'failure':
                    consecutive_failures += 1
                    if consecutive_failures >= CONSECUTIVE_FAILURE_ABORT:
                        logging.error(
                            f"{consecutive_failures} consecutive fetches failed — HMDS appears down. "
                            f"Aborting to avoid silent data loss; restart IB Gateway and re-run. "
                            f"Last attempted: {symbol} on {target_dt} (row {idx})."
                        )
                        break
                elif status == 'ok':
                    consecutive_failures = 0

                ib.sleep(PACING_SLEEP_SECONDS)
            else:
                prev_close = cache[cache_key]

            if prev_close is not None:
                df.at[idx, 'LastDailyClose'] = prev_close
                filled += 1
            else:
                failed += 1

    finally:
        if ib.isConnected():
            ib.disconnect()
            logging.info("Disconnected.")

    out_dir = args.output_dir if args.output_dir else os.path.dirname(os.path.abspath(args.input_file))
    input_stem = os.path.splitext(os.path.basename(args.input_file))[0]
    out_path = os.path.join(out_dir, f"{input_stem}_pricefilled.tsv")
    df.to_csv(out_path, sep='\t', index=False)

    logging.info(
        f"Done: {filled} filled, {failed} failed, "
        f"{skipped_present} already present, {skipped_exchange} non-US exchange. "
        f"Unique (symbol, date) lookups: {len(cache)}."
    )
    logging.info(f"Saved to {out_path}")


if __name__ == '__main__':
    main()
