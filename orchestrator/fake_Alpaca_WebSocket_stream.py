# =============================================================================
# fake_Alpaca_WebSocket_stream.py
#
# Monkey-patches alpaca.data.live.news.NewsDataStream with a fake
# implementation that replays a JSON file at a scheduled trigger_time.
#
# Import this module BEFORE importing NewsWatcher2 (see Orchestrator.py).
# NewsWatcher2 is never modified — it receives FakeNewsDataStream transparently.
#
# JSON file format (raw Alpaca — passes through NW2's 7 filters as-is):
#   [
#     {
#       "id": 51390044,
#       "headline": "Citigroup Maintains Buy on AAL",
#       "author": "Benzinga Newsdesk",
#       "symbols": ["AAL"],
#       "summary": "",
#       "content": "",
#       "created_at": "2026-03-20T19:41:04Z",
#       "updated_at": "2026-03-20T19:41:05Z",
#       "url": "https://...",
#       "source": "benzinga"
#     }
#   ]
# =============================================================================

# ── User-configurable options ─────────────────────────────────────────────────
trigger_time = "13:44:00"     # hh:mm:ss local time — when items are fired
json_file    = "/home/tom/Documents/ibkr_scripts/N1/scripts/historicalNewsFetch/outputs/_RED-DAY_ALBT_26-feb-2026_4.json"  # path to JSON file (absolute or relative to cwd)
# ─────────────────────────────────────────────────────────────────────────────

import asyncio
import json
import logging
from datetime import datetime, timedelta

logger = logging.getLogger('FakeStream')


class FakeNewsItem:
    """
    Mimics the Alpaca news object passed to NW2's _handle_news async callback.
    All attributes are set from a plain dict; NW2 accesses them via getattr().
    """
    def __init__(self, d: dict):
        self.id         = d.get('id')
        self.headline   = d.get('headline', '')
        self.summary    = d.get('summary', '')
        self.content    = d.get('content', '')
        self.author     = d.get('author', '')
        self.created_at = d.get('created_at')
        self.updated_at = d.get('updated_at')
        self.url        = d.get('url', '')
        self.symbols    = d.get('symbols', [])
        self.source     = d.get('source', '')

    def __repr__(self):
        return f"FakeNewsItem(id={self.id}, symbols={self.symbols}, headline={self.headline!r:.60})"


class FakeNewsDataStream:
    """
    Drop-in replacement for alpaca.data.live.news.NewsDataStream.

    Interface matched:
      __init__(api_key, secret_key)   — no-op, no network connection
      subscribe_news(cb, *symbols)    — stores the async callback
      _run_forever()                  — async coroutine; waits until
                                        trigger_time, fires all items,
                                        then idles (stays alive)
      close()                         — async no-op
    """

    def __init__(self, api_key=None, secret_key=None):
        self._callback = None
        logger.info(
            f"FakeNewsDataStream initialised (api_key ignored, no network connection)"
        )

    def subscribe_news(self, callback, *symbols):
        self._callback = callback
        logger.info(
            f"subscribe_news registered (symbols arg ignored — all items in JSON will be fired)"
        )

    async def _run_forever(self):
        # ── Wait until trigger_time ───────────────────────────────────────────
        target_time = datetime.strptime(trigger_time, "%H:%M:%S").time()
        now         = datetime.now()
        target_dt   = datetime.combine(now.date(), target_time)

        if target_dt <= now:
            target_dt += timedelta(days=1)   # already past today → fire tomorrow

        delay = (target_dt - now).total_seconds()
        logger.info(
            f"Trigger scheduled at {trigger_time} — waiting {delay:.1f}s "
            f"(fires at {target_dt.strftime('%Y-%m-%d %H:%M:%S')})"
        )
        await asyncio.sleep(delay)

        # ── Load JSON file ────────────────────────────────────────────────────
        try:
            with open(json_file, 'r') as f:
                data = json.load(f)
        except FileNotFoundError:
            logger.error(f"JSON file not found: {json_file!r}")
            while True:
                await asyncio.sleep(3600)
        except json.JSONDecodeError as exc:
            logger.error(f"Invalid JSON in {json_file!r}: {exc}")
            while True:
                await asyncio.sleep(3600)

        items = data if isinstance(data, list) else [data]
        logger.info(f"Firing {len(items)} item(s) from {json_file!r}")

        # ── Fire items through NW2's registered async callback ────────────────
        for item in items:
            news_obj = FakeNewsItem(item)
            logger.debug(f"  → {news_obj}")
            if self._callback is not None:
                await self._callback(news_obj)

        logger.info("All items fired. Stream idle — press Ctrl+C to exit.")

        # ── Stay alive so NW2's asyncio task loop keeps running ───────────────
        while True:
            await asyncio.sleep(3600)

    async def close(self):
        logger.info("FakeNewsDataStream.close() called — no-op.")


# ── Install monkey-patch (runs at import time, before NewsWatcher2 imports) ──
import alpaca.data.live.news as _alpaca_news_module
_alpaca_news_module.NewsDataStream = FakeNewsDataStream
logger.info(
    "Monkey-patch installed: alpaca.data.live.news.NewsDataStream → FakeNewsDataStream"
)
print(
    "[FakeStream] Monkey-patch active: NewsDataStream replaced with FakeNewsDataStream\n"
    f"             trigger_time = {trigger_time}\n"
    f"             json_file    = {json_file}"
)
