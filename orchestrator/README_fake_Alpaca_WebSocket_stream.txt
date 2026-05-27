================================================================================
 README — fake_Alpaca_WebSocket_stream.py
================================================================================

PURPOSE
-------
Simulates the Alpaca WebSocket news stream for offline testing and pipeline
replay. News items from a local JSON file are injected at a scheduled time
and flow through NewsWatcher2's full 7-filter pipeline — exactly as real
Alpaca news would. NewsWatcher2.py is NOT modified.

MECHANISM
---------
Monkey-patching: when fake_Alpaca_WebSocket_stream.py is imported, it
replaces alpaca.data.live.news.NewsDataStream in memory with FakeNewsDataStream
before NewsWatcher2 imports it. NewsWatcher2 calls subscribe_news() and
_run_forever() on the fake class and never knows it is not the real stream.

    Orchestrator.py
      └── import fake_Alpaca_WebSocket_stream     ← patches alpaca module
      └── import NewsWatcher2 as nw               ← gets FakeNewsDataStream
            └── nw.start() → NewsDataStream(...)  ← creates fake instance
                  └── _run_forever()              ← waits, then fires items
                        └── NW2's _handle_news()  ← 7 filters run normally
                              └── on_news_accepted() callback


FILES CHANGED
-------------
  fake_Alpaca_WebSocket_stream.py   (NEW)   — fake stream + monkey-patch
  Orchestrator.py                   (MODIFIED) — added FAKE_STREAM option


HOW TO USE
----------

Step 1 — Set options in fake_Alpaca_WebSocket_stream.py:

    trigger_time = "14:30:00"          # hh:mm:ss local time to fire items
    json_file    = "fake_news.json"    # path to JSON file (absolute or relative)

Step 2 — Enable the fake stream in Orchestrator.py:

    FAKE_STREAM = 'YES'    # line ~17

Step 3 — Run:

    python3 Orchestrator.py

Step 4 — Restore real stream when done:

    FAKE_STREAM = 'NO'


JSON FILE FORMAT
----------------
The file must be a JSON array of raw Alpaca-format news objects. Each item
passes through NW2's 7 real filters before reaching the Orchestrator callback.

  [
    {
      "id":         51390044,
      "headline":   "Citigroup Maintains Buy on American Airlines Group",
      "author":     "Benzinga Newsdesk",
      "symbols":    ["AAL"],
      "summary":    "",
      "content":    "",
      "created_at": "2026-03-20T19:41:04Z",
      "updated_at": "2026-03-20T19:41:05Z",
      "url":        "https://www.benzinga.com/...",
      "source":     "benzinga"
    }
  ]

A single JSON object (not wrapped in an array) is also accepted.


NW2 FILTER REQUIREMENTS (items must pass all 7 to reach the callback)
----------------------------------------------------------------------
  1. Unique ID          — id not seen before in this run
  2. Author             — must be "Benzinga Newsdesk" (default allowed list)
  3. Single symbol      — symbols[] must contain exactly 1 ticker
  4. Headline only      — summary AND content must be empty / whitespace
  5. Universe check     — the symbol must be in the active stock universe
  6. Not blacklisted    — symbol must not be on the blacklist
  7. No excluded strings— headline must not contain words like "halted", "halt", etc.


BEHAVIOR AT TRIGGER TIME
------------------------
- All items in the JSON file are fired back-to-back with no delay.
- After all items are sent, the fake stream stays alive and idle.
- Press Ctrl+C to trigger the normal graceful shutdown.
- If trigger_time has already passed today, firing is scheduled for the
  same time tomorrow.
- If the JSON file is missing or malformed, an error is logged and the
  stream idles (no crash, Ctrl+C still works).


CONSOLE OUTPUT (on startup with FAKE_STREAM='YES')
--------------------------------------------------
  [FakeStream] Monkey-patch active: NewsDataStream replaced with FakeNewsDataStream
               trigger_time = 14:30:00
               json_file    = fake_news.json

  ... (NW2 startup logs) ...

  INFO FakeStream: Trigger scheduled at 14:30:00 — waiting 47.3s (fires at 2026-04-05 14:30:00)
  INFO FakeStream: Firing 3 item(s) from 'fake_news.json'
  INFO FakeStream: All items fired. Stream idle — press Ctrl+C to exit.

  ============================================================
  NEWS ITEM PROCESSED
    Symbol      : AAL
    ID          : 51390044
    ArrivalTime : 2026-04-05 14:30:00.123456
    Headline    : Citigroup Maintains Buy on American Airlines Group
    Echo1       : ...
    Echo2       : ...
  ============================================================


CLASSES
-------
  FakeNewsItem
    Plain object mimicking Alpaca's news item. Exposes all attributes
    read by NW2 via getattr(): id, headline, summary, content, author,
    created_at, updated_at, url, symbols, source.

  FakeNewsDataStream
    Drop-in for alpaca.data.live.news.NewsDataStream.
      __init__(api_key, secret_key)  — no-op, no network
      subscribe_news(cb, *symbols)   — stores the async callback
      _run_forever()                 — async: waits → fires items → idles
      close()                        — async no-op


DEPENDENCIES
------------
  Standard library only: asyncio, json, logging, datetime
  No network connection required when FAKE_STREAM='YES'.
================================================================================
