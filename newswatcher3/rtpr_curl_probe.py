#!/usr/bin/env python3
"""
rtpr_curl_probe.py — concurrency-sweep isolation experiment for the RTPR
permalink-curl latency.

Why this exists
---------------
Every RTPR WS user must now curl the signed permalink (pure-WS JSON delivery is
dead).  On 2026-06-16 07:00 our curl of SUGP took 21.5s (two 10s timeouts +
success) while another RTPR user curling the SAME endpoint for the SAME article
got it in milliseconds.  Same endpoint, same article, same moment ⇒ the endpoint
is fine and the latency is OURS — our client, our connection, our egress IP, or
how we behave when ~11 curls fire at once.

This probe settles WHICH of those it is by measuring single vs concurrent curls
from THIS host, each decomposed per connection stage (dns / queued / connect+TLS /
ttfb) with an aiohttp TraceConfig — the breakdown NW4's opaque `curl=` number and
the empty-message asyncio.TimeoutError could never give.

Run it with NW4 STOPPED (RTPR allows one WS connection per key) at a top-of-hour
so the burst fills the collect buffer with fresh, unexpired permalinks fast.

  Phase 1 COLLECT : buffer the next --collect alert article_urls (NO curl yet).
  Phase 2 SWEEP   : replay them at each --levels concurrency, in two modes —
                      shared = one session w/ NW4's connector (keepalive reuse)
                      fresh  = force_close connector (new connection per request)
  Phase 3 REPORT  : per-request rows → --out CSV; summary + verdict to stdout.

Reading the result
------------------
  L=1 already slow (seconds)   → host / network / egress-IP baseline (not code);
                                 compare against a fast peer's host.
  L=1 fast (~ms) but L=N slow  → burst/concurrency-triggered; the dominant stage
                                 at L=N names the cause:
       queued  → connector pool starvation (limit / limit_per_host)
       connect → TLS stampede or per-IP concurrent-conn cap (CDN/WAF)
       dns     → resolver / DNS-cache stampede
       ttfb    → server-side per-IP/key throttle on us

Usage:
    /home/tom/venv/bin/python rtpr_curl_probe.py --collect 12 --levels 1,3,12 \
        --out /tmp/curlprobe.csv
"""

import argparse
import asyncio
import csv
import json
import statistics
import sys
import time
from pathlib import Path
from urllib.parse import urlparse, parse_qs

try:
    import aiohttp
    import websockets
except ImportError as exc:  # pragma: no cover
    print(f"Missing dependency: {exc}. pip install aiohttp websockets", file=sys.stderr)
    sys.exit(1)

# Re-use NW3's key parser so a key file written for NW3/NW4 works for the probe.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from NewsWatcher3 import _load_rtpr_credentials  # noqa: E402

WS_URL = "wss://ws.rtpr.io/ws-alerts?apiKey={key}"


# ─── TraceConfig: per-request connection-lifecycle stamps ─────────────────────
# Self-contained copy of NewsWatcher4's tracer so this probe runs independently of
# the live module.
def _make_trace_config() -> aiohttp.TraceConfig:
    def _stamp(key):
        async def _cb(session, ctx, params):
            d = getattr(ctx, "trace_request_ctx", None)
            if isinstance(d, dict):
                d.setdefault(key, time.monotonic())
        return _cb

    async def _mark_reused(session, ctx, params):
        d = getattr(ctx, "trace_request_ctx", None)
        if isinstance(d, dict):
            d["reused"] = True

    tc = aiohttp.TraceConfig()
    tc.on_request_start.append(_stamp("t_start"))
    tc.on_dns_resolvehost_start.append(_stamp("t_dns_start"))
    tc.on_dns_resolvehost_end.append(_stamp("t_dns_end"))
    tc.on_connection_queued_start.append(_stamp("t_queued_start"))
    tc.on_connection_queued_end.append(_stamp("t_queued_end"))
    tc.on_connection_create_start.append(_stamp("t_create_start"))
    tc.on_connection_create_end.append(_stamp("t_create_end"))
    tc.on_connection_reuseconn.append(_mark_reused)
    tc.on_response_chunk_received.append(_stamp("t_ttfb"))
    tc.on_request_end.append(_stamp("t_end"))
    return tc


def _dur(ctx, a, b):
    ta, tb = ctx.get(a), ctx.get(b)
    return (tb - ta) if (ta is not None and tb is not None) else None


def _stage_durations(ctx: dict) -> dict:
    start, ttfb = ctx.get("t_start"), ctx.get("t_ttfb")
    return {
        "dns":     _dur(ctx, "t_dns_start", "t_dns_end"),
        "queued":  _dur(ctx, "t_queued_start", "t_queued_end"),
        "connect": _dur(ctx, "t_create_start", "t_create_end"),
        "ttfb":    (ttfb - start) if (start is not None and ttfb is not None) else None,
    }


def _exp_of(url: str):
    try:
        return int(parse_qs(urlparse(url).query).get("exp", [None])[0])
    except Exception:
        return None


# ─── Fetch / sweep ────────────────────────────────────────────────────────────
async def _timed_fetch(session, url, api_key, timeout_sec) -> dict:
    ctx: dict = {}
    headers = {"X-API-Key": api_key, "Accept": "application/json"}
    t0 = time.monotonic()
    status, err, nbytes = None, None, 0
    try:
        async with session.get(url, headers=headers, trace_request_ctx=ctx,
                               timeout=aiohttp.ClientTimeout(total=timeout_sec)) as resp:
            status = resp.status
            nbytes = len(await resp.read())
    except Exception as e:
        err = type(e).__name__
    elapsed = time.monotonic() - t0
    return {
        "elapsed": elapsed,
        "status": status if status is not None else err,
        "bytes": nbytes,
        "reused": ctx.get("reused", False),
        "url": url,
        **_stage_durations(ctx),
    }


async def _run_level(urls, api_key, level, mode, timeout_sec) -> list:
    if mode == "fresh":
        connector = aiohttp.TCPConnector(limit=level, force_close=True)
    else:  # "shared": mirror NW4's connector so reuse behaves identically
        connector = aiohttp.TCPConnector(limit=64, limit_per_host=64,
                                         keepalive_timeout=300)
    sem = asyncio.Semaphore(level)

    async with aiohttp.ClientSession(connector=connector,
                                     trace_configs=[_make_trace_config()]) as session:
        async def _one(u):
            async with sem:
                r = await _timed_fetch(session, u, api_key, timeout_sec)
                r["level"], r["mode"] = level, mode
                return r
        return await asyncio.gather(*[_one(u) for u in urls])


async def _collect(ws, n, deadline_s) -> list:
    urls = []
    end = time.monotonic() + deadline_s
    print(f"[probe] COLLECT: want {n} alerts within {deadline_s:.0f}s "
          f"(NW4 should be STOPPED)…")
    while len(urls) < n:
        remaining = end - time.monotonic()
        if remaining <= 0:
            print(f"[probe] collect deadline hit with {len(urls)} url(s).")
            break
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=min(35.0, remaining))
        except asyncio.TimeoutError:
            continue
        try:
            msg = json.loads(raw)
        except Exception:
            continue
        mtype = msg.get("type")
        if mtype == "ping":
            await ws.send(json.dumps({"type": "pong"}))
            continue
        if mtype != "alert":
            continue
        url = msg.get("article_url")
        if not url:
            continue
        urls.append(url)
        print(f"[probe]  buffered {len(urls)}/{n}  ticker={msg.get('ticker')}  "
              f"exp={_exp_of(url)}")
    return urls


# ─── Reporting ────────────────────────────────────────────────────────────────
def _med(vals):
    vals = [v for v in vals if v is not None]
    return statistics.median(vals) if vals else None


def _pct(vals, p):
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return None
    k = max(0, min(len(vals) - 1, int(round((p / 100.0) * (len(vals) - 1)))))
    return vals[k]


def _fmt(x):
    return f"{x:.3f}" if isinstance(x, (int, float)) else "—"


def _print_level_summary(rows, level, mode):
    tot = [r["elapsed"] for r in rows]
    ok = sum(1 for r in rows if r["status"] == 200)
    print(f"[probe]   level={level} mode={mode}: n={len(rows)} ok={ok} "
          f"med={_fmt(_med(tot))}s p95={_fmt(_pct(tot, 95))}s max={_fmt(max(tot))}s | "
          f"dns(med)={_fmt(_med([r['dns'] for r in rows]))} "
          f"queued(med)={_fmt(_med([r['queued'] for r in rows]))} "
          f"connect(med)={_fmt(_med([r['connect'] for r in rows]))} "
          f"ttfb(med)={_fmt(_med([r['ttfb'] for r in rows]))}")


def _write_csv(path, rows):
    fields = ["mode", "level", "status", "elapsed", "dns", "queued", "connect",
              "ttfb", "reused", "bytes", "url"]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _dominant_stage(rows):
    """Return (label, disjoint-bucket-medians). aiohttp's stages nest/overlap —
    `connect` (on_connection_create) contains `dns`, and `ttfb` (request→first byte)
    contains queued+connect+server — so picking the max *raw* duration always names
    ttfb. We split into DISJOINT buckets first: queued, dns, tcp_tls (=connect−dns),
    server (=ttfb−connect−queued). Labels map back to the hint table:
    tcp_tls→'connect', server→'ttfb'."""
    def _b_queued(r):  return r["queued"]
    def _b_dns(r):     return r["dns"]
    def _b_tcptls(r):
        return None if r["connect"] is None else max(0.0, r["connect"] - (r["dns"] or 0.0))
    def _b_server(r):
        if r["ttfb"] is None:
            return None
        return max(0.0, r["ttfb"] - (r["connect"] or 0.0) - (r["queued"] or 0.0))

    buckets = {
        "queued":  _med([_b_queued(r) for r in rows]) or 0.0,
        "dns":     _med([_b_dns(r) for r in rows]) or 0.0,
        "connect": _med([_b_tcptls(r) for r in rows]) or 0.0,   # TCP+TLS only
        "ttfb":    _med([_b_server(r) for r in rows]) or 0.0,    # server only
    }
    return max(buckets, key=lambda k: buckets[k]), buckets


def _verdict(all_rows, levels):
    lo, hi = min(levels), max(levels)

    def med_tot(level, mode="shared"):
        return _med([r["elapsed"] for r in all_rows
                     if r["level"] == level and r["mode"] == mode])

    lo_med, hi_med = med_tot(lo), med_tot(hi)
    print("\n[probe] ───── VERDICT ─────")
    print(f"[probe] shared: L={lo} med={_fmt(lo_med)}s  vs  L={hi} med={_fmt(hi_med)}s")
    if lo_med is None:
        print("[probe] inconclusive — no shared-mode data.")
        return
    if lo_med >= 2.0:
        print("[probe] L=1 is already SLOW → host / network / egress-IP baseline; "
              "compare against a fast peer's host. NOT a concurrency or code issue.")
        return
    if hi_med is not None and hi_med >= max(2.0, 3 * lo_med):
        rows_hi = [r for r in all_rows if r["level"] == hi and r["mode"] == "shared"]
        dom, buckets = _dominant_stage(rows_hi)
        hint = {
            "queued":  "connector pool starvation (limit / limit_per_host)",
            "connect": "TLS stampede or per-IP concurrent-conn cap (CDN/WAF)",
            "dns":     "resolver / DNS-cache stampede",
            "ttfb":    "server-side per-IP/key throttle on us",
        }[dom]
        disjoint = "  ".join(f"{k}={_fmt(v)}" for k, v in buckets.items())
        print(f"[probe] L=1 fast but L={hi} slow → concurrency-triggered.")
        print(f"[probe] disjoint buckets @L={hi} (med s): {disjoint}")
        print(f"[probe] dominant='{dom}' → likely cause: {hint}")
        return
    print("[probe] L=1 fast and no strong concurrency effect → could not reproduce "
          "the burst stall this run; retry at a busier top-of-hour.")


async def probe(api_keys_path, collect_n, levels, modes, timeout_sec,
                collect_timeout, out_csv) -> int:
    api_key = _load_rtpr_credentials(api_keys_path)
    print(f"[probe] key loaded; connecting to {WS_URL.format(key='<redacted>')}")
    async with websockets.connect(WS_URL.format(key=api_key), ping_interval=None) as ws:
        print("[probe] WS connected.")
        urls = await _collect(ws, collect_n, collect_timeout)
    # WS is now closed — sweeping uses HTTP only, so the single-connection-per-key
    # limit no longer matters and curls don't compete with the alert stream.
    if not urls:
        print("[probe] no alerts collected — run at a top-of-hour. exiting.")
        return 1

    exps = [e for e in (_exp_of(u) for u in urls) if e]
    if exps:
        print(f"[probe] earliest permalink exp in ~{min(exps) - int(time.time())}s "
              f"— sweeping now ({len(urls)} urls).")

    all_rows = []
    for mode in modes:
        for level in levels:
            print(f"[probe] SWEEP level={level} mode={mode} …")
            rows = await _run_level(urls, api_key, level, mode, timeout_sec)
            all_rows.extend(rows)
            _print_level_summary(rows, level, mode)

    _write_csv(out_csv, all_rows)
    print(f"[probe] wrote {len(all_rows)} rows → {out_csv}")
    _verdict(all_rows, levels)
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="RTPR permalink curl concurrency-sweep isolation probe.")
    ap.add_argument(
        "--api-keys",
        default=str(Path(__file__).resolve().parent / "RTPR_API-Key.txt"),
        help="Path to the RTPR API key file (default: ./RTPR_API-Key.txt).")
    ap.add_argument("--collect", type=int, default=12,
                    help="Number of alert article_urls to buffer before sweeping.")
    ap.add_argument("--levels", default="1,3,12",
                    help="Comma list of concurrency levels to sweep (default 1,3,12).")
    ap.add_argument("--modes", default="shared,fresh",
                    help="Comma list of connector modes: shared,fresh (default both).")
    ap.add_argument("--timeout", type=float, default=30.0,
                    help="Per-request total timeout in seconds (default 30; > NW4's "
                         "10s so the probe sees the true single-attempt latency).")
    ap.add_argument("--collect-timeout", type=float, default=240.0,
                    help="Max seconds to wait while collecting alerts (default 240).")
    ap.add_argument("--out", default="/tmp/curlprobe.csv",
                    help="Per-request CSV output path (default /tmp/curlprobe.csv).")
    args = ap.parse_args()

    levels = [int(x) for x in args.levels.split(",") if x.strip()]
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    try:
        rc = asyncio.run(probe(args.api_keys, args.collect, levels, modes,
                               args.timeout, args.collect_timeout, args.out))
    except KeyboardInterrupt:
        print("\n[probe] interrupted")
        rc = 130
    sys.exit(rc)


if __name__ == "__main__":
    main()
