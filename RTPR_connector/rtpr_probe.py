#!/usr/bin/env python3
"""
rtpr_probe.py — one-shot verifier for the RTPR ws-alerts + permalink-curl flow.

Run this ONCE after creating the catch-all rule on https://rtpr.io/wire and
BEFORE running Orchestrator3.4 in anger.  Updated 2026-05-25: the permalink
fetch returns HTML (Next.js page), not JSON, so the probe now dumps the raw
bytes to disk regardless of content-type and tries 3 alternate fetches to
rule out a hidden JSON variant.

Sequence:
  1. Connect to wss://ws.rtpr.io/ws-alerts?apiKey=<key>.
  2. Reply to server pings to stay alive.
  3. Wait for the first {"type":"alert", ...} message.
  4. PRIMARY FETCH — curl the article_url with X-API-Key + Accept:
     application/json (NW4's current behavior).
  5. Dump the response body to <out>.primary.html and print a marker verdict
     block: __NEXT_DATA__ | application/ld+json | self.__next_f.push( |
     og:title | og:description | og:article:published_time | <article> tag.
  6. ALTERNATE FETCH 1 — same URL, Accept: application/json ONLY (no
     text/html, no */*).
  7. ALTERNATE FETCH 2 — URL + ?format=json (or & if exp/sig already there).
  8. ALTERNATE FETCH 3 — same URL, X-Requested-With: XMLHttpRequest.
  9. For each fetch, print HTTP status, Content-Type, Content-Length and
     dump body to <out>.alt{1,2,3}.html.
 10. Exit.

The verdict block tells NW4's `_normalize_article` which scraping strategy
to use; the dumped HTML files let us iterate offline without re-hitting
RTPR.

Usage:
    python3 rtpr_probe.py
    python3 rtpr_probe.py --api-keys ./RTPR_API-Key.txt --out /tmp/rtpr_curl_probe
"""

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

try:
    import aiohttp
    import websockets
except ImportError as exc:  # pragma: no cover
    print(f"Missing dependency: {exc}. pip install aiohttp websockets", file=sys.stderr)
    sys.exit(1)

# Re-use NW3's key parser so a key file written for NW3 works for the probe.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from NewsWatcher3 import _load_rtpr_credentials  # noqa: E402

WS_URL = "wss://ws.rtpr.io/ws-alerts?apiKey={key}"

# Markers we look for in the HTML response, in priority order. The first
# strategy that finds a usable blob will drive _normalize_article in NW4.
MARKERS = [
    ('__NEXT_DATA__',        re.compile(rb'<script[^>]*id="__NEXT_DATA__"[^>]*type="application/json"[^>]*>', re.I)),
    ('JSON-LD',              re.compile(rb'<script[^>]*type="application/ld\+json"[^>]*>',                  re.I)),
    ('RSC __next_f.push',    re.compile(rb'self\.__next_f\.push\(')),
    ('og:title',             re.compile(rb'<meta[^>]+property="og:title"',                                  re.I)),
    ('og:description',       re.compile(rb'<meta[^>]+property="og:description"',                            re.I)),
    ('og:published_time',    re.compile(rb'<meta[^>]+property="(?:og:)?article:published_time"',            re.I)),
    ('<article> tag',        re.compile(rb'<article[\s>]',                                                  re.I)),
    ('<main> tag',           re.compile(rb'<main[\s>]',                                                     re.I)),
]


def _scan_markers(html_bytes: bytes) -> list[tuple[str, bool, int]]:
    """Return [(name, present, count)] for each marker."""
    results = []
    for name, rx in MARKERS:
        matches = rx.findall(html_bytes)
        results.append((name, bool(matches), len(matches)))
    return results


def _print_marker_verdict(html_bytes: bytes) -> None:
    print("\n[probe] ───── HTML markers in primary response ─────")
    for name, present, count in _scan_markers(html_bytes):
        flag = '✔' if present else '✘'
        print(f"  {flag}  {name:<22}  matches={count}")
    # Bonus: title tag content
    m = re.search(rb'<title[^>]*>([^<]+)</title>', html_bytes, re.I)
    if m:
        try:
            title = m.group(1).decode('utf-8', errors='replace').strip()
        except Exception:
            title = repr(m.group(1)[:80])
        print(f"  •  <title>: {title[:140]}")


def _add_format_json(url: str) -> str:
    """Append ?format=json or &format=json to the URL."""
    sep = '&' if '?' in url else '?'
    return f"{url}{sep}format=json"


async def _do_fetch(
    http: aiohttp.ClientSession,
    label: str,
    url: str,
    headers: dict,
    out_path: Path,
) -> bytes | None:
    """One fetch + dump body + print one-line summary. Returns body bytes."""
    print(f"\n[probe] ─── {label} ───")
    print(f"[probe] GET {url[:140]}")
    print(f"[probe] headers: {headers}")
    try:
        async with http.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            body = await resp.read()
            ctype = resp.headers.get('Content-Type', '<none>')
            print(f"[probe] HTTP {resp.status}  Content-Type={ctype!r}  bytes={len(body)}")
            out_path.write_bytes(body)
            print(f"[probe] body dumped → {out_path}")
            # If the response actually IS JSON, summarize the top-level keys.
            if 'json' in ctype.lower():
                try:
                    data = json.loads(body)
                    if isinstance(data, dict):
                        print(f"[probe] response is JSON dict; keys: {sorted(data.keys())}")
                    elif isinstance(data, list):
                        print(f"[probe] response is JSON list of length {len(data)}")
                except Exception as e:
                    print(f"[probe] declared JSON but parse failed: {e}")
            return body
    except Exception as e:
        print(f"[probe] FETCH FAILED: {type(e).__name__}: {e}")
        return None


async def probe(api_keys_path: str, out_stem: str) -> int:
    api_key = _load_rtpr_credentials(api_keys_path)
    print(f"[probe] key loaded; connecting to {WS_URL.format(key='<redacted>')}")

    out_stem_path = Path(out_stem)
    out_primary = out_stem_path.with_suffix('.primary.html')
    out_alt1    = out_stem_path.with_suffix('.alt1.json-only.html')
    out_alt2    = out_stem_path.with_suffix('.alt2.format-json.html')
    out_alt3    = out_stem_path.with_suffix('.alt3.xhr.html')

    async with aiohttp.ClientSession() as http:
        async with websockets.connect(WS_URL.format(key=api_key), ping_interval=None) as ws:
            print("[probe] WS connected — waiting for first 'alert' message…")
            while True:
                raw = await ws.recv()
                try:
                    msg = json.loads(raw)
                except Exception as e:
                    print(f"[probe] failed to parse msg: {e}; raw={str(raw)[:200]}")
                    continue

                mtype = msg.get('type')
                if mtype == 'ping':
                    await ws.send(json.dumps({'type': 'pong'}))
                    continue
                if mtype == 'connected':
                    print(f"[probe] RTPR connected: plan={msg.get('plan')}")
                    continue
                if mtype == 'subscribed':
                    print(f"[probe] subscribed: {msg.get('message', '')}")
                    continue
                if mtype != 'alert':
                    print(f"[probe] non-alert msg type={mtype!r}: {str(msg)[:200]}")
                    continue

                # First alert — print + run all 4 fetches + exit.
                print("\n[probe] ───── ALERT ─────")
                print(json.dumps(msg, indent=2))

                article_url = msg.get('article_url')
                if not article_url:
                    print("[probe] alert has no article_url — cannot curl. exiting.")
                    return 1

                # === PRIMARY FETCH ===
                primary_headers = {'X-API-Key': api_key, 'Accept': 'application/json'}
                primary_body = await _do_fetch(http, "PRIMARY (X-API-Key + Accept: application/json)",
                                               article_url, primary_headers, out_primary)
                if primary_body is not None:
                    _print_marker_verdict(primary_body)

                # === ALTERNATE 1: Accept: application/json only (drop X-API-Key fallback hint) ===
                # Same URL+key but Accept set with q values that disallow HTML.  Useful in case
                # the server content-negotiates strictly.
                alt1_headers = {
                    'X-API-Key': api_key,
                    'Accept': 'application/json, text/json;q=0.9, */*;q=0.0',
                }
                await _do_fetch(http, "ALT 1 (Accept: application/json only)",
                                article_url, alt1_headers, out_alt1)

                # === ALTERNATE 2: URL + ?format=json ===
                alt2_url = _add_format_json(article_url)
                alt2_headers = {'X-API-Key': api_key, 'Accept': 'application/json'}
                await _do_fetch(http, "ALT 2 (?format=json query string)",
                                alt2_url, alt2_headers, out_alt2)

                # === ALTERNATE 3: X-Requested-With: XMLHttpRequest ===
                alt3_headers = {
                    'X-API-Key': api_key,
                    'Accept': 'application/json',
                    'X-Requested-With': 'XMLHttpRequest',
                }
                await _do_fetch(http, "ALT 3 (X-Requested-With: XMLHttpRequest)",
                                article_url, alt3_headers, out_alt3)

                print("\n[probe] ───── SUMMARY ─────")
                print(f"[probe] primary → {out_primary}")
                print(f"[probe] alt 1  → {out_alt1}")
                print(f"[probe] alt 2  → {out_alt2}")
                print(f"[probe] alt 3  → {out_alt3}")
                print("[probe] DONE — paste the marker verdict and the 4 status/content-type lines back.")
                return 0


def main():
    ap = argparse.ArgumentParser(description="One-shot RTPR alerts WS + permalink fetch probe (HTML-aware).")
    ap.add_argument(
        '--api-keys',
        default=str(Path(__file__).resolve().parent / 'RTPR_API-Key.txt'),
        help="Path to the RTPR API key file (default: ./RTPR_API-Key.txt next to this script).",
    )
    ap.add_argument(
        '--out',
        default='/tmp/rtpr_curl_probe',
        help=("Stem for dumped response files. Suffixes .primary.html, .alt1.json-only.html, "
              ".alt2.format-json.html, .alt3.xhr.html are appended. "
              "Default: /tmp/rtpr_curl_probe"),
    )
    args = ap.parse_args()

    try:
        rc = asyncio.run(probe(args.api_keys, args.out))
    except KeyboardInterrupt:
        print("\n[probe] interrupted")
        rc = 130
    sys.exit(rc)


if __name__ == '__main__':
    main()
