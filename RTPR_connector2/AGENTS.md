For the work for this project I dont want you to get content or access to any other information outside of this directory to avoid recreate past mistakes.

# RTPR_connector2.py implementation notes

Treat this project as isolated. Do not inspect git state, previous branches,
commit history, stored memory, or unrelated external context when working on
`RTPR_connector2.py`.

`RTPR_connector2.py` is an asyncio-based RTPR.io WebSocket firehose connector.
Its architecture is:

1. One WebSocket listener task owns the RTPR connection.
2. Alerts are pushed into an unbounded in-memory `asyncio.Queue`.
3. A pool of async article workers fetches RTPR article URLs through one shared
   `aiohttp.ClientSession`.
4. Workers push parsed result dictionaries into a result queue.
5. One dataframe writer task is the only component allowed to mutate the global
   `PR_dataframe`.

The script must keep the WebSocket hot path minimal. The listener handles RTPR
`ping` messages with `pong`, logs connection events, parses only what is needed,
filters the original RTPR alert `ticker` against the universe set, records
`ArrivalTime`, and enqueues accepted alerts. It must not fetch articles, write
files, or mutate the dataframe.

Startup behavior:

- Read the RTPR API key automatically from `./RTPR_API-Key.txt`.
- On startup, select the latest file matching `stocks_universe_YYYY-MM-DD.tsv`
  from `../universe_finder/data`, then read only its `Symbol` column.
- Cache symbols in a Python `set` for O(1) membership checks.
- Reload the latest matching universe file every day at `03:30:00` fixed UTC-4
  and atomically swap the in-memory symbol set after the file has loaded.
- Default worker settings are `--workers 50` and
  `--max-concurrent-fetches 100`.
- Run forever until interrupted.

Article fetching behavior:

- Use asyncio and `aiohttp`, not per-request `curl`.
- Reuse the shared HTTP session for keep-alive and DNS caching.
- Fetch each `article_url` exactly as RTPR sent it.
- Send the `X-API-Key` header.
- Do not retry failed article fetches.
- Log failed fetches and skipped articles.

Parsing behavior:

- Parse tickers from the fetched RTPR article HTML page.
- Keep `Symbol` as the original RTPR alert ticker.
- Parse `ID` from the fetched article footer text such as
  `Article ID: nACS0Gsm4a`, falling back to the URL path if needed.
- Parse `Headline` from the HTML `<title>`.
- Store `Body` as plain text with HTML removed.
- If the fetched HTML tickers do not intersect the current universe set, skip the PR
  and log the skip.

`PR_dataframe` columns and order:

`Symbol, Tickers, ID, ArrivalDate, Created, ArrivalTime, CurlTime, Headline, Body`

Time handling:

- Use fixed UTC-4, not daylight-saving timezone conversion.
- Format time fields with millisecond precision.
- `ArrivalDate` and `Created` are derived from RTPR
  `article_published_at`.
- `ArrivalTime` is when the WebSocket alert was received by the script.
- `CurlTime` is when the article fetch completed.

Logging:

- Logs are written under `./logs` with daily filenames formatted as
  `RTPR_connector2_YYYY_MM_DD.log` using the fixed UTC-4 date.
- Logging uses a queue-based non-blocking handler so disk writes do not block
  ingestion or fetch workers.
- Successful PR fetch lines follow this shape:

```text
2026-06-30 08:00:22.425 INFO    PR ANY    nNFCbcp6QW 233ms created=08:00:22.064 arrival=08:00:22.190 curl=08:00:22.424 tickers=["ANY"]
```

- Also log failed fetches, skipped tickers, reconnects, and heartbeat events.

Daily flush:

- At `20:35` fixed UTC-4, write the current dataframe to disk and clear memory.
- Write TSV content to `./tables/PR_dataframe_YYYY-MM-DD.csv`.
- Preserve the requested `.csv` filename even though the delimiter is tab.
- The dataframe writer should swap the active dataframe before disk write so
  ingestion and fetch workers continue without waiting on file I/O.

Validation notes:

- `python3 -m py_compile RTPR_connector2.py` passed after implementation.
- `python3 RTPR_connector2.py --self-test` passed using
  `RTPR_url_fetch_raw_output.txt`.
- The local environment blocked system-wide installation of `beautifulsoup4`,
  so the implemented parser uses Python's standard `html.parser` instead of a
  BeautifulSoup dependency.
