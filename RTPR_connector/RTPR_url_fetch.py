#!/usr/bin/env python3
"""
RTPR_url_fetch.py — capture the next RTPR alert and dump the RAW content of its URL.

Each RTPR ws-alerts frame carries `article_url`, a signed short-lived permalink
(https://rtpr.io/a/<id>?exp=<ts>&sig=<sig>). This one-shot tool opens its own
connection to the alerts WebSocket, waits for the FIRST live alert, fetches that
URL exactly once (sending X-API-Key so it resolves even past the ~2-min signature
window), and writes the raw response body to disk for inspection. No parsing, no
scraping — just the bytes that sit at the other end of the URL.

It writes TWO files (next to this script by default):
  * RTPR_url_fetch_output.txt           — the raw response body, byte-for-byte.
  * RTPR_url_fetch_output_with_meta.txt  — a short metadata header, a separator,
                                           then the identical raw body.
Fetch metadata (HTTP status / Content-Type / byte count) also prints to the console.

IMPORTANT: RTPR allows only ONE ws-alerts connection per account, and
RTPR_connector.py occupies it while running. Stop the connector before running
this script, or this connection may be rejected (close code 4001/4004) — or it
will bump the connector off its feed. Alerts are sparse outside market hours; the
busiest window is the 04:00 ET pre-market open.

Usage:
    python3 RTPR_url_fetch.py
    python3 RTPR_url_fetch.py --api-keys ./RTPR_API-Key.txt --out-dir . --timeout 20
    python3 RTPR_url_fetch.py --max-wait 600     # give up if no alert in 10 min
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    import aiohttp
    import websockets
except ImportError as exc:  # pragma: no cover
    print(f"Missing dependency: {exc}. pip install aiohttp websockets", file=sys.stderr)
    sys.exit(1)

WS_URL = "wss://ws.rtpr.io/ws-alerts?apiKey={key}"
PONG = json.dumps({"type": "pong"})
DISPLAY_TZ = ZoneInfo("America/New_York")  # matches RTPR rendering + the US market clock
OUT_STEM = "RTPR_url_fetch_output"


def load_api_key(path: Path) -> str:
    """Read the RTPR API key (the line starting with 'rtpr_') from the key file.

    Mirrors load_api_key() in RTPR_connector.py:187 so a key file written for the
    connector works here unchanged.
    """
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("rtpr_"):
                return line
    raise RuntimeError(f"No RTPR API key (line starting with 'rtpr_') found in {path}")


async def wait_for_alert(ws) -> dict:
    """Read frames until the first 'alert' with an article_url; answer pings meanwhile."""
    while True:
        raw = await ws.recv()
        try:
            msg = json.loads(raw)
        except Exception as e:
            print(f"[fetch] skipping unparseable frame: {e}; raw={str(raw)[:160]}")
            continue

        mtype = msg.get("type")
        if mtype == "ping":
            await ws.send(PONG)
            continue
        if mtype == "connected":
            print(f"[fetch] RTPR connected (plan={msg.get('plan')})")
            continue
        if mtype == "subscribed":
            print(f"[fetch] subscribed: {msg.get('message', '')}")
            continue
        if mtype != "alert":
            print(f"[fetch] ignoring non-alert frame type={mtype!r}")
            continue
        if not msg.get("article_url"):
            print("[fetch] alert had no article_url — waiting for the next one…")
            continue
        return msg


async def fetch_url(http: aiohttp.ClientSession, url: str, api_key: str, timeout: float):
    """Single GET of the signed permalink. Returns (status, content_type, body_bytes)."""
    headers = {"X-API-Key": api_key, "Accept": "text/html"}
    async with http.get(
        url, headers=headers, timeout=aiohttp.ClientTimeout(total=timeout)
    ) as resp:
        body = await resp.read()
        ctype = resp.headers.get("Content-Type", "<none>")
        return resp.status, ctype, body


def build_meta_header(url, ticker, published_at, status, ctype, nbytes) -> str:
    bar = "=" * 60
    fetched_at = datetime.now(DISPLAY_TZ).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    return (
        f"{bar}\n"
        "RTPR URL FETCH — metadata\n"
        f"{bar}\n"
        f"Fetched at      : {fetched_at} {DISPLAY_TZ.key}\n"
        f"Article URL     : {url}\n"
        f"Ticker          : {ticker}\n"
        f"Published at    : {published_at}\n"
        f"HTTP status     : {status}\n"
        f"Content-Type    : {ctype}\n"
        f"Body bytes      : {nbytes}\n"
        f"{bar}\n"
        "--- RAW BODY BELOW ---\n"
    )


async def run(api_keys_path: str, out_dir: str, timeout: float, max_wait) -> int:
    api_key = load_api_key(Path(api_keys_path))
    print(f"[fetch] API key loaded (…{api_key[-4:]}).")
    print(
        "[fetch] NOTE: RTPR allows only ONE ws-alerts connection per account — "
        "stop RTPR_connector.py first, or this connection may be rejected."
    )

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    raw_path = out / f"{OUT_STEM}.txt"
    meta_path = out / f"{OUT_STEM}_with_meta.txt"

    alert = None
    try:
        async with websockets.connect(
            WS_URL.format(key=api_key), ping_interval=None, max_size=2**20
        ) as ws:
            print("[fetch] WS connected — waiting for the next 'alert'… (Ctrl-C to stop)")
            if max_wait:
                alert = await asyncio.wait_for(wait_for_alert(ws), timeout=max_wait)
            else:
                alert = await wait_for_alert(ws)
    except asyncio.TimeoutError:
        print(f"[fetch] no alert received within {max_wait:.0f}s — exiting.")
        return 1
    except (websockets.exceptions.WebSocketException, OSError) as e:
        print(f"[fetch] WS connection failed: {type(e).__name__}: {e}")
        print(
            "[fetch] hint: is RTPR_connector.py still running (one WS per account)? "
            "or is the API key invalid/expired?"
        )
        return 1

    if not alert:
        return 1

    article_url = alert["article_url"]
    ticker = alert.get("ticker")
    published_at = alert.get("article_published_at")
    print(f"[fetch] ALERT received: ticker={ticker!r} published_at={published_at!r}")
    print(f"[fetch] article_url = {article_url}")

    try:
        async with aiohttp.ClientSession() as http:
            status, ctype, body = await fetch_url(http, article_url, api_key, timeout)
    except Exception as e:
        print(f"[fetch] FETCH FAILED: {type(e).__name__}: {e}")
        return 1

    header = build_meta_header(article_url, ticker, published_at, status, ctype, len(body))
    raw_path.write_bytes(body)
    meta_path.write_bytes(header.encode("utf-8") + body)

    print()
    print(f"[fetch] HTTP {status}  Content-Type={ctype!r}  bytes={len(body)}")
    if status != 200:
        print("[fetch] WARNING: non-200 status — body may be an error page (written anyway).")
    print(f"[fetch] raw body  → {raw_path}")
    print(f"[fetch] body+meta → {meta_path}")
    return 0


def main():
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(
        description="Capture the next RTPR alert and dump the raw content of its article_url."
    )
    ap.add_argument(
        "--api-keys",
        default=str(here / "RTPR_API-Key.txt"),
        help="Path to the RTPR API key file (default: ./RTPR_API-Key.txt next to this script).",
    )
    ap.add_argument(
        "--out-dir",
        default=str(here),
        help=f"Directory for the two output files (default: this script's dir). "
        f"Writes {OUT_STEM}.txt and {OUT_STEM}_with_meta.txt.",
    )
    ap.add_argument(
        "--timeout",
        type=float,
        default=20.0,
        help="HTTP fetch timeout in seconds (default: 20).",
    )
    ap.add_argument(
        "--max-wait",
        type=float,
        default=None,
        help="Max seconds to wait for an alert before giving up (default: wait indefinitely).",
    )
    args = ap.parse_args()

    try:
        rc = asyncio.run(run(args.api_keys, args.out_dir, args.timeout, args.max_wait))
    except KeyboardInterrupt:
        print("\n[fetch] interrupted")
        rc = 130
    sys.exit(rc)


if __name__ == "__main__":
    main()
