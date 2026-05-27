# Architecture Diagram

## System Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    pronounCer System (v2.0)                  │
└─────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│ CLIENT LAYER (pronounCer.py)                                 │
├──────────────────────────────────────────────────────────────┤
│                                                               │
│  Input Files (FEED_28-jan-2026_*.txt)                        │
│           ↓                                                   │
│  ┌─────────────────────────────────┐                         │
│  │ Process File (parallel)         │                         │
│  │  • Read input                   │                         │
│  │  • HTTP POST to /resolve        │                         │
│  │  • Write output file            │                         │
│  └──────────────┬──────────────────┘                         │
│                 │                                             │
│  Output Files (FEED_28-jan-2026_*_pronouns.txt)              │
│                                                               │
└──────────────┬──────────────────────────────────────────────┘
               │
               │ HTTP Requests (Port 5050)
               ↓
┌──────────────────────────────────────────────────────────────┐
│ SERVICE LAYER (pronounCer_service.py)                        │
├──────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  Flask HTTP Server (localhost:5050)                 │    │
│  │                                                      │    │
│  │  GET  /health      → Health check                   │    │
│  │  GET  /            → Service info                   │    │
│  │  POST /config      → Set mode (simple/full)        │    │
│  │  POST /resolve     → Process text                   │    │
│  └─────────────────────────────────────────────────────┘    │
│                          │                                    │
│     ┌────────────────────┼────────────────────┐              │
│     ↓                    ↓                    ↓              │
│  ┌─────────────┐    ┌─────────────┐    ┌──────────────┐    │
│  │ Config      │    │ Resolver    │    │ Model Cache  │    │
│  │ Endpoint    │    │ Dispatcher  │    │              │    │
│  │             │    │             │    │ • spaCy NLP  │    │
│  │ Sets:       │    │ Routes to:  │    │ • fastcoref  │    │
│  │ resolver    │    │ • Simple    │    │   (optional) │    │
│  │ resolver_   │    │ • Full      │    │              │    │
│  │ mode        │    │             │    │              │    │
│  └─────────────┘    └──────┬──────┘    └──────────────┘    │
│                             │                                 │
│        ┌────────────────────┼────────────────────┐            │
│        ↓                    ↓                                  │
│  ┌──────────────────┐  ┌──────────────────┐                 │
│  │ PronounResolver  │  │ FastCorefResolver│                 │
│  │ (Simple Mode)    │  │ (Full Mode)      │                 │
│  │                  │  │                  │                 │
│  │ Uses:            │  │ Uses:            │                 │
│  │ • spaCy NER      │  │ • fastcoref      │                 │
│  │ • Heuristics     │  │ • Transformers   │                 │
│  │                  │  │                  │                 │
│  │ Resolves:        │  │ Resolves:        │                 │
│  │ ✓ Pronouns      │  │ ✓ Pronouns      │                 │
│  │ ✗ Noun phrases  │  │ ✓ Noun phrases  │                 │
│  │                  │  │                  │                 │
│  │ Performance:     │  │ Performance:     │                 │
│  │ ~0.3-0.5s/file  │  │ ~1-2s/file       │                 │
│  └──────────────────┘  └──────────────────┘                 │
│                                                               │
│  Resolver Selection Logic:                                    │
│                                                               │
│  if mode == "simple":                                        │
│      use PronounResolver                                     │
│  else if mode == "full":                                     │
│      if fastcoref_available:                                 │
│          use FastCorefResolver                               │
│      else:                                                    │
│          fallback to PronounResolver (with warning)          │
│                                                               │
└──────────────────────────────────────────────────────────────┘
```

## Request/Response Flow

```
CLIENT REQUEST FLOW:

1. Client calls configure_service("full")
   ↓
2. Client sends: POST /config {"mode": "full"}
   ↓
3. Service receives, calls initialize_model("full")
   ↓
4. Service checks: if FASTCOREF_AVAILABLE?
   ├─ YES → resolver = FastCorefResolver()
   └─ NO  → resolver = PronounResolver(), mode = "simple"
   ↓
5. Service responds: {"mode": "full|simple", "status": "success"}
   ↓
6. Client confirms mode set, proceeds to file processing
   ↓
7. For each file, client sends: POST /resolve {"text": "..."}
   ↓
8. Service routes to current resolver.resolve_text()
   ↓
9. Resolver processes and returns modified text
   ↓
10. Service responds: {"resolved_text": "...", "mode": "...", "status": "success"}
   ↓
11. Client writes output file
```

## Data Flow Example: Simple vs Full Mode

```
INPUT TEXT:
"ENvue Medical announced earnings. It beat expectations. The company grew revenue."

───────────────────────────────────────────────────────────────

SIMPLE MODE (PronounResolver):

  Text → spaCy NLP Pipeline
         ├─ Tokenize
         ├─ POS tagging
         ├─ Dependency parsing
         └─ NER (Named Entity Recognition)

         Output: Entities detected
         ├─ "ENvue Medical" → ORG
         ├─ "It" → PRON
         └─ "company" → NOUN

  → Find Pronouns (PRON tokens)
    ├─ "It" → Score entities
    └─ Nearest ORG = "ENvue Medical"

  → Replace
    "It" → "ENvue Medical"

OUTPUT (Simple):
"ENvue Medical announced earnings. ENvue Medical beat expectations. The company grew revenue."
                                                                    ↑ NOT resolved (noun phrase)

───────────────────────────────────────────────────────────────

FULL MODE (FastCorefResolver):

  Text → fastcoref Transformer Model
         (Trained on large coreference dataset)

         Output: Coreference Clusters
         Cluster 1: ["ENvue Medical", "It", "The company"]

  → Build Replacement Mapping
    Canonical (first) = "ENvue Medical"
    ├─ "It" → "ENvue Medical"
    └─ "The company" → "ENvue Medical"

  → Replace All
    "It" → "ENvue Medical"
    "The company" → "ENvue Medical"

OUTPUT (Full):
"ENvue Medical announced earnings. ENvue Medical beat expectations. ENvue Medical grew revenue."
                                                                    ↑ RESOLVED! (noun phrase)
```

## Configuration State Machine

```
┌─────────────────────────────────────────────────────────┐
│         Resolver Configuration State Machine            │
└─────────────────────────────────────────────────────────┘

Initial State: Simple Mode
┌──────────────────────┐
│  resolver =          │
│  PronounResolver     │
│  resolver_mode =     │
│  "simple"            │
└──────┬───────────────┘
       │
       │ POST /config {"mode": "full"}
       │
       ↓
┌──────────────────────────────────────────┐
│  fastcoref_available = True?             │
├──────────────────────────────────────────┤
│  YES                  NO                  │
├────────────┬──────────────┬──────────────┤
│            │              │              │
↓            ↓              ↓              │
load    log warning    use              │
fastcoref   fallback    Pronoun         │
         to simple       Resolver       │
│                        │              │
│  ┌──────────────┐    ┌─────────────┐  │
│  │ resolver =   │    │ resolver =  │  │
│  │ FastCoref    │    │ Pronoun     │  │
│  │ resolver_    │    │ resolver_   │  │
│  │ mode =       │    │ mode =      │  │
│  │ "full"       │    │ "simple"    │  │
│  └──────────────┘    └─────────────┘  │
│         │                 │            │
└─────────┴─────────────────┴────────────┘
        │                │
        │ POST /resolve  │
        │                │
        ↓                ↓
   Use FastCoref    Use Pronoun
     Resolver        Resolver
        │                │
        └────────┬───────┘
                 │
            return resolved_text
                 + mode info
```

## Deployment Architecture

```
┌──────────────────────────────────────────────────────────┐
│                    Typical Deployment                    │
└──────────────────────────────────────────────────────────┘

User's Machine
┌────────────────────────────────────────────────────────┐
│  Terminal 1: Service                                   │
│  ┌──────────────────────────────────────────────────┐ │
│  │ $ python3 pronounCer_service.py                 │ │
│  │ Loading spaCy model...                          │ │
│  │ Model loaded successfully!                      │ │
│  │ Starting Flask server on http://localhost:5050  │ │
│  │ Press Ctrl+C to stop the service                │ │
│  │                          (running continuously) │ │
│  └──────────────────────────────────────────────────┘ │
│                                                         │
│  Terminal 2: Client (run as needed)                    │
│  ┌──────────────────────────────────────────────────┐ │
│  │ $ python3 pronounCer.py --inputs FEED_28...     │ │
│  │ Connecting to service...                        │ │
│  │ Processing 3 files in parallel...               │ │
│  │ ✓ All files processed successfully!             │ │
│  │                          (runs, exits) ────┐    │ │
│  └──────────────────────────────────────────────────┘ │
│                                                         │
│  Local Files:                                           │
│  ├─ FEED_28-jan-2026_headline.txt     (input)         │
│  ├─ FEED_28-jan-2026_summary.txt      (input)         │
│  ├─ FEED_28-jan-2026_content.txt      (input)         │
│  ├─ FEED_28-jan-2026_headline_pronouns.txt (output)   │
│  ├─ FEED_28-jan-2026_summary_pronouns.txt  (output)   │
│  └─ FEED_28-jan-2026_content_pronouns.txt  (output)   │
│                                                         │
│  Available Models (in memory):                          │
│  ├─ spaCy: en_core_web_sm (~50MB always loaded)       │
│  └─ fastcoref: Optional (~2GB if enabled, ~1GB used)  │
│                                                         │
└────────────────────────────────────────────────────────┘
```

## Feature Comparison Matrix

```
┌──────────────────────────────────────────────────────────┐
│            Feature Support by Mode                       │
├──────────────────────────────────────────────────────────┤
│ Feature                    │  Simple  │  Full  │  Notes  │
├────────────────────────────┼──────────┼────────┼─────────┤
│ Pronoun resolution         │    ✓     │   ✓    │         │
│ (he, she, it, they, etc)   │          │        │         │
├────────────────────────────┼──────────┼────────┼─────────┤
│ Definite noun phrases      │    ✗     │   ✓    │ Requires│
│ (the company, the firm)    │          │        │fastcoref│
├────────────────────────────┼──────────┼────────┼─────────┤
│ Complex coreference chains │    ~     │   ✓    │ Simple: │
│                            │          │        │ heuristic
├────────────────────────────┼──────────┼────────┼─────────┤
│ Processing speed (sec/file)│   0.3-0.5│  1-2   │ Simple  │
│                            │          │        │ 3-4x    │
│                            │          │        │ faster  │
├────────────────────────────┼──────────┼────────┼─────────┤
│ Memory usage               │   200MB  │ 1.5GB  │ Simple  │
│                            │          │        │ 7.5x    │
│                            │          │        │ lighter │
├────────────────────────────┼──────────┼────────┼─────────┤
│ Heavy dependencies         │    No    │  Yes   │ ~2GB    │
│ (PyTorch, transformers)    │          │        │         │
├────────────────────────────┼──────────┼────────┼─────────┤
│ Accuracy (general)         │   Good   │ Excellent
│                            │          │        │ ~85%    │
│                            │          │        │ vs ~95% │
├────────────────────────────┼──────────┼────────┼─────────┤
│ Use case: fast processing  │    ✓     │   ✗    │         │
│ Use case: high accuracy    │    ✗     │   ✓    │         │
└────────────────────────────┴──────────┴────────┴─────────┘

Legend:
✓  = Fully supported
~  = Partially supported
✗  = Not supported
```

## Scaling Architecture (Future)

```
┌─────────────────────────────────────────────────────────┐
│      Potential Multi-Instance Deployment (Future)       │
└─────────────────────────────────────────────────────────┘

                    Load Balancer
                        │
         ┌──────────────┼──────────────┐
         ↓              ↓              ↓
    Instance 1     Instance 2    Instance 3
    (Port 5050)    (Port 5051)   (Port 5052)
    ┌────────────┐┌────────────┐┌────────────┐
    │ Service    ││ Service    ││ Service    │
    │ Resolver:  ││ Resolver:  ││ Resolver:  │
    │ Simple     ││ Simple     ││ Full       │
    │ or Full    ││ or Full    ││ (GPU)      │
    └────────────┘└────────────┘└────────────┘

    Shared:
    - Configuration database
    - Model cache (distributed)
    - Results cache
    - Metrics/monitoring
```

---

This architecture provides:
- ✅ Clear separation of concerns
- ✅ Extensibility for future enhancements
- ✅ Simple mode for common use cases
- ✅ Full mode for advanced needs
- ✅ Graceful degradation on missing features
- ✅ Error handling and recovery
