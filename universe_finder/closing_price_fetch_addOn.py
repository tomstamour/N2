import argparse
import asyncio
import logging
import math
import os
import sys

import pandas as pd
from ib_insync import IB, Stock

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)


def build_output_path(input_path: str) -> str:
    root, ext = os.path.splitext(input_path)
    return f"{root}_priced{ext}"


def connect(host: str, port: int, client_id: int) -> IB:
    ib = IB()
    log.info(f"Connecting to IBKR at {host}:{port} clientId={client_id} ...")
    try:
        ib.connect(host, port, clientId=client_id, timeout=20)
    except ConnectionRefusedError:
        log.error(f"Connection refused — is TWS/Gateway running on port {port}?")
        sys.exit(1)
    except Exception as exc:
        log.error(f"Connection failed: {exc}")
        sys.exit(1)
    if not ib.isConnected():
        log.error("connect() returned but IB reports not connected.")
        sys.exit(1)
    log.info("Connected.")
    return ib


def ensure_connected(ib: IB, host: str, port: int, client_id: int,
                     max_wait: int = 180) -> bool:
    """Reconnect if the API connection has dropped.

    Retries with a fixed backoff until reconnected or max_wait seconds elapse.
    Handles IB Gateway's nightly restart and transient drops that would
    otherwise turn every subsequent request into a "Not connected" failure.
    Returns True if connected on return, False otherwise.
    """
    if ib.isConnected():
        return True
    waited = 0
    while waited < max_wait:
        try:
            ib.disconnect()
        except Exception:
            pass
        try:
            ib.connect(host, port, clientId=client_id, timeout=20)
            if ib.isConnected():
                log.warning("Reconnected to IBKR after drop.")
                return True
        except Exception as exc:
            log.warning(f"Reconnect attempt failed ({exc}); retrying in 15s …")
        ib.sleep(15)
        waited += 15
    log.error(f"Could not reconnect to IBKR within {max_wait}s.")
    return False


def fetch_last_rth_close(ib: IB, symbol: str) -> float:
    contract = Stock(symbol, 'SMART', 'USD')
    try:
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            log.warning(f"{symbol}: could not qualify contract — skipping")
            return float('nan')
        contract = qualified[0]
    except Exception as exc:
        log.warning(f"{symbol}: qualifyContracts error ({exc}) — skipping")
        return float('nan')

    try:
        bars = ib.reqHistoricalData(
            contract,
            endDateTime='',
            durationStr='2 D',
            barSizeSetting='1 day',
            whatToShow='TRADES',
            useRTH=True,
            formatDate=1,
            keepUpToDate=False,
            timeout=30,
        )
    except Exception as exc:
        log.warning(f"{symbol}: reqHistoricalData error ({exc}) — skipping")
        return float('nan')

    if not bars:
        log.warning(f"{symbol}: no data returned — skipping")
        return float('nan')

    price = bars[-1].close
    log.info(f"{symbol}: {price}")
    return price


async def fetch_last_rth_close_async(ib: IB, symbol: str, sem: asyncio.Semaphore,
                                     timeout: int = 15, retries: int = 2) -> tuple:
    """Async sibling of fetch_last_rth_close for concurrent batch fetching.

    `sem` bounds the number of in-flight requests. A failed qualifyContracts is
    treated as a permanent bad-symbol error (no retry); an empty/timed-out
    historical request is retried up to `retries` times before giving up.

    Returns ``(price, qualified_contract)``. The qualified ``Stock`` is returned
    even when the historical fetch fails, so a downstream step can reuse it and
    skip a second qualify round-trip; both elements are NaN/None when qualify
    itself failed.
    """
    async with sem:
        contract = Stock(symbol, 'SMART', 'USD')
        try:
            qualified = await ib.qualifyContractsAsync(contract)
            if not qualified:
                return float('nan'), None
            contract = qualified[0]
        except asyncio.CancelledError:
            raise
        except Exception:
            return float('nan'), None

        # Pass `timeout` into reqHistoricalDataAsync directly instead of
        # wrapping it in an outer asyncio.wait_for: ib_insync's own internal
        # timeout handler calls `cancelHistoricalData(reqId)` on expiry, which
        # releases the server-side reqId slot. The previous outer-wait_for
        # pattern propagated CancelledError into ib_insync before its
        # TimeoutError branch could run, leaking reqIds until the Gateway hit
        # its 50-simultaneous-historical-requests cap and went silent. On
        # timeout, ib_insync returns an empty BarDataList (not an exception),
        # so `if bars:` is the right check. (Same fix as fetch_freq_async in
        # trade_frequency_addOn.py — do NOT reintroduce asyncio.wait_for here.)
        for attempt in range(retries + 1):
            try:
                bars = await ib.reqHistoricalDataAsync(
                    contract,
                    endDateTime='',
                    durationStr='2 D',
                    barSizeSetting='1 day',
                    whatToShow='TRADES',
                    useRTH=True,
                    formatDate=1,
                    keepUpToDate=False,
                    timeout=timeout,
                )
                if bars:
                    return bars[-1].close, contract
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            if attempt < retries:
                await asyncio.sleep(1.0)
        return float('nan'), contract


def main():
    parser = argparse.ArgumentParser(
        description="Append LastDailyClosePrice column to a symbol TSV using IBKR historical data."
    )
    parser.add_argument('--input-table', required=True, metavar='PATH',
                        help='Input TSV file (Symbol/Exchange/Float_M/Float_Source columns)')
    parser.add_argument('--clientID', type=int, default=10, metavar='ID',
                        help='IBKR API client ID (default: 10)')
    parser.add_argument('--port', type=int, default=7497, metavar='PORT',
                        help='TWS/Gateway port (default: 7497 TWS, 4002 Gateway)')
    parser.add_argument('--host', default='127.0.0.1', metavar='HOST',
                        help='IBKR host (default: 127.0.0.1)')
    args = parser.parse_args()

    if not os.path.isfile(args.input_table):
        log.error(f"Input file not found: {args.input_table}")
        sys.exit(1)

    df = pd.read_csv(args.input_table, sep='\t')
    if 'Symbol' not in df.columns:
        log.error("Input TSV must have a 'Symbol' column.")
        sys.exit(1)

    symbols = df['Symbol'].tolist()
    log.info(f"Loaded {len(symbols)} symbols from {args.input_table}")

    ib = connect(args.host, args.port, args.clientID)
    prices = []
    try:
        for symbol in symbols:
            prices.append(fetch_last_rth_close(ib, symbol))
            ib.sleep(0.5)
    finally:
        ib.disconnect()
        log.info("Disconnected.")

    df['LastDailyClosePrice'] = prices

    succeeded = sum(1 for p in prices if not (p != p))  # NaN check
    failed = len(prices) - succeeded
    log.info(f"Done: {succeeded} prices fetched, {failed} failed/missing.")

    output_path = build_output_path(args.input_table)
    df.to_csv(output_path, sep='\t', index=False)
    log.info(f"Output written to {output_path}")


if __name__ == '__main__':
    main()
