# Orchestrator Architecture

Visual reference for `Orchestrator.py`. Four Mermaid diagrams, each answering a different question:

1. **Component / module map** — what scripts and external systems are involved, and what calls/reads/writes what.
2. **Per-news sequence** — exactly what happens on the wire and on the threads when one news item arrives.
3. **Threading / concurrency** — which threads and processes exist, and how they synchronize.
4. **Data-flow pipeline** — how a news_dict gets enriched into the TSV row that drives the trade-mole trigger.

For prose-level documentation see [`CLAUDE.md`](./CLAUDE.md). The diagrams below are not duplicated as text — read the source if you need deeper detail.

---

## 1. Component / Module Map

```mermaid
flowchart TD
    subgraph Orch["Orchestrator.py"]
        main[main]
        on_news[on_news_accepted]
        finbert_fn[analyze_finbert]
        echo2_fn[echo2]
        tf_fn[analyze_trade_frequency]
        collect[_collect_and_log]
        tsv_fn[_append_to_tsv]
        tm_fn[maybe_launch_trade_mole]
    end

    subgraph Pipeline["Pipeline scripts"]
        NW2["NewsWatcher2.py<br/>register_callback / start / stop"]
        FB["FinBERT-headliner.py<br/>load_model / analyze_headline"]
        PTB["pre_trade_frequency_baseline.py<br/>PreBaselineApp / make_contract / compute_pre_stats"]
        TMS["trade_mole.py<br/>(CLI subprocess)"]
        FAKE["fake_Alpaca_WebSocket_stream.py<br/>(only when FAKE_STREAM='YES')"]
        YF["yfinance_stock_universe.py<br/>(disabled — nasdaq TSV used instead)"]
    end

    subgraph Ext["External systems"]
        ALP["Alpaca News WebSocket<br/>alpaca.data.live.news.NewsDataStream"]
        IBKR["IBKR Gateway<br/>127.0.0.1:4001"]
        HF["HuggingFace ProsusAI/finbert<br/>./finbert_onnx/ (ONNX INT8)"]
    end

    subgraph FilesIn["Input files"]
        NASDAQ["nasdaq_symbols_data.tsv"]
        EXC["excluded_strings.txt"]
        BL["black_list.csv"]
        KEYS["alpaca_API-Keys.txt"]
    end

    subgraph FilesOut["Output files"]
        TSVOUT["outputs/news_output_DATE.tsv"]
        TFCSV["outputs/SYMBOL_*.csv (TF)"]
        TFLOG["logs/SYMBOL_*.log (TF)"]
        TMOUT["trade_mole_outputs/"]
        TMLOG["trade_mole_logs/"]
        NWOUT["newswatcher2/outputs/ (NW2 dumps)"]
    end

    main -->|read_csv| NASDAQ
    main -->|read| EXC
    main -->|load_model| FB
    main -->|register_callback + start| NW2

    NW2 -->|WebSocket subscribe| ALP
    NW2 -->|reads| BL
    NW2 -->|reads| KEYS
    NW2 -->|periodic flush| NWOUT
    NW2 -.->|fires callback| on_news
    FAKE -.->|monkey-patches NewsDataStream| NW2

    on_news -->|submit| finbert_fn
    on_news -->|submit| echo2_fn
    on_news -->|submit| tf_fn
    on_news -->|submit| collect

    finbert_fn -->|analyze_headline| FB
    FB -->|loads ONNX model| HF

    tf_fn -->|PreBaselineApp| PTB
    PTB -->|reqHistoricalTicks| IBKR
    tf_fn -->|writes| TFCSV
    tf_fn -->|writes| TFLOG

    collect --> tsv_fn
    tsv_fn -->|append row| TSVOUT
    collect --> tm_fn
    tm_fn -.->|subprocess.Popen detached| TMS
    TMS -->|reqTickByTickData| IBKR
    TMS -->|writes| TMOUT
    TMS -->|writes| TMLOG

    main -.->|currently disabled| YF
```

---

## 2. Per-News Sequence

```mermaid
sequenceDiagram
    autonumber
    participant Alpaca as Alpaca WS
    participant NW2 as NW2 thread<br/>(asyncio daemon)
    participant Orch as on_news_accepted<br/>(runs in NW2 thread)
    participant Pool as ThreadPoolExecutor<br/>(max_workers=6)
    participant IBKR as IBKR Gateway
    participant TSV as news_output_DATE.tsv
    participant TM as trade_mole<br/>(detached subprocess)

    Alpaca->>NW2: news event
    Note over NW2: 7 filters<br/>(universe, blacklist,<br/>excluded_strings, dedup, ...)
    NW2->>Orch: on_news_accepted(news_dict)

    Orch->>Pool: submit(analyze_finbert)
    Orch->>Pool: submit(echo2)
    Orch->>Pool: submit(analyze_trade_frequency)
    Orch->>Pool: submit(_collect_and_log, futures)
    Orch-->>NW2: returns immediately (NW2 unblocked)

    par FinBERT worker
        Pool->>Pool: analyze_headline (model singleton)
    and Echo2 worker
        Pool->>Pool: echo2 (placeholder)
    and TF worker
        Pool->>IBKR: connect(clientId = 999 + N)
        Pool->>IBKR: reqHistoricalTicks (TF_TICKS_QUANTITY)
        IBKR-->>Pool: ticks
        opt fewer ticks than requested AND TF_CROSS_SESSION
            Pool->>IBKR: reqHistoricalTicks (previous session)
            IBKR-->>Pool: more ticks (deduped + merged)
        end
        Pool->>Pool: compute_pre_stats → trades_per_sec
        Pool->>IBKR: disconnect
    end

    Pool->>Pool: _collect_and_log waits on 3 futures
    Note over Pool: Float lookup in _nasdaq_df<br/>build completed_dict (11 cols + Echo2)
    Pool->>TSV: _append_to_tsv (under _tsv_lock)

    alt sentiment_score > 0.2 AND Float < 100M AND tps > 0
        Pool->>TM: subprocess.Popen(start_new_session=True)
        Note over TM: own pgid — survives Ctrl+C on parent
        TM->>IBKR: reqTickByTickData (clientID = 400 + N)
    else gate fails
        Note over Pool: skip launch, log only
    end
```

---

## 3. Threading / Concurrency

```mermaid
flowchart TB
    subgraph Main["Main thread"]
        direction TB
        m1[main] --> m2[load_model — FinBERT singleton]
        m2 --> m3[read nasdaq_symbols_data.tsv → universe]
        m3 --> m4[nw.register_callback]
        m4 --> m5[nw.start]
        m5 --> m6[install SIGINT / SIGTERM handlers]
        m6 --> m7[_stop_event.wait]
        m7 -->|signal received| m8[executor.shutdown wait=True]
        m8 --> m9[nw.stop]
    end

    subgraph NW2T["NW2 background thread (daemon)"]
        direction TB
        n1[Alpaca asyncio loop] --> n2[7 filters]
        n2 --> n3[on_news_accepted — synchronous]
    end

    subgraph Exec["ThreadPoolExecutor — max_workers=6, prefix=echo_worker"]
        direction TB
        w1[analyze_finbert]
        w2[echo2]
        w3[analyze_trade_frequency]
        w4[_collect_and_log]
    end

    subgraph TFT["Per-TF-call ibapi thread<br/>(daemon, tf-ibapi-CLIENTID)"]
        t1[app.run — IBKR EClient loop]
    end

    subgraph TMP["Detached subprocess (start_new_session=True)"]
        p1[trade_mole.py<br/>own pgid, own log, own clientID]
    end

    n3 -->|submit x4| Exec
    w3 -.->|spawn then join 3s| t1
    w4 -.->|Popen, no wait| p1

    sig[/"SIGINT / SIGTERM"/] --> ev[("_stop_event")]
    ev --> m7

    L1[("_tsv_lock<br/>guards TSV append")]:::lock --- w4
    L2[("_tf_clientid_lock + itertools.count from 999")]:::lock --- w3
    L3[("_tm_clientid_lock + itertools.count from 400")]:::lock --- w4
    L4[("Futures: f_finbert / f_echo2 / f_tradefreq")]:::lock --- w4

    classDef lock fill:#fef3c7,stroke:#b45309,stroke-width:1px
```

---

## 4. Data-Flow Pipeline

```mermaid
flowchart LR
    A["Alpaca news event<br/>{Symbol, ID, ArrivalTime,<br/>Headline, ...}"]
    A --> B["NW2 7-filter pipeline"]
    B -->|news_dict| C{{"on_news_accepted"}}

    C -->|analyze_finbert| F1["+ positive, negative,<br/>neutral, sentiment_score, label"]
    C -->|echo2| F2["+ Echo2"]
    C -->|analyze_trade_frequency<br/>via IBKR reqHistoricalTicks| F3["+ Trades/sec"]
    C -->|_nasdaq_df lookup on Symbol| F4["+ Float (millions)"]

    F1 --> M["completed_dict<br/>(_TSV_COLUMNS + Echo2)"]
    F2 --> M
    F3 --> M
    F4 --> M

    M -->|_append_to_tsv<br/>under _tsv_lock| TSV[("outputs/news_output_DATE.tsv")]
    M --> G{{"maybe_launch_trade_mole<br/>sentiment_score > 0.2<br/>AND Float < 100<br/>AND Trades/sec > 0"}}
    G -->|pass| SP[["trade_mole.py subprocess<br/>--symbol --clientID --lifeTime<br/>--baseline-trade-per-second<br/>start_new_session=True"]]
    G -->|fail| X([skip, log only])

    SP --> TMOUT[("trade_mole_outputs/")]
    SP --> TMLOG[("trade_mole_logs/")]
```
