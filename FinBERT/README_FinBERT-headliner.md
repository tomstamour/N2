# FinBERT-headliner.py

Headline-only financial sentiment analysis using ProsusAI/finbert (ONNX INT8 quantized).

Extracts the `"headline"` field from raw news JSON files and runs FinBERT inference directly — no NER annotation required.

---

## Prerequisites

```bash
pip install --break-system-packages numpy transformers optimum[onnxruntime]
```

---

## Setup — One-Time Model Export

Shares the same `finbert_onnx/` directory as `FinBERT-analysis.py`. Skip this step if already exported.

```bash
python FinBERT-headliner.py --export
```

Downloads `ProsusAI/finbert` from HuggingFace, converts to ONNX FP32, quantizes to INT8.
Output: `./finbert_onnx/`

---

## CLI Usage

### Analyse a raw news JSON file
```bash
python FinBERT-headliner.py --input ./FEED_28-jan-2026_1.json
```
Output file: `./FEED_28-jan-2026_1_headline_sentiment.json` (auto-named)

### Specify a custom output path
```bash
python FinBERT-headliner.py --input ./FEED_28-jan-2026_1.json --output ./result.json
```

### Analyse a headline string directly
```bash
python FinBERT-headliner.py --headline "Apple reports record quarterly earnings"
```

---

## Library Usage

```python
from FinBERT_headliner import analyze_headline, analyze_news_file, load_model

# Optional: pre-warm the model before first inference
load_model()

# Analyse a headline string
scores = analyze_headline("Apple reports record quarterly earnings")

# Analyse from a raw news JSON file
result = analyze_news_file("./FEED_28-jan-2026_1.json")
```

### Return shape
```json
{
  "headline":        "Apple reports record quarterly earnings",
  "positive":        0.8214,
  "negative":        0.0312,
  "neutral":         0.1474,
  "sentiment_score": 0.7902,
  "label":           "positive"
}
```

| Field | Type | Description |
|---|---|---|
| `headline` | str | Original headline text |
| `positive` | float | Probability [0–1] |
| `negative` | float | Probability [0–1] |
| `neutral` | float | Probability [0–1] |
| `sentiment_score` | float | `positive − negative` (range: −1 to +1) |
| `label` | str | `"positive"` / `"negative"` / `"neutral"` |

`analyze_news_file()` also adds a `"source_file"` field with the file path.

### Label threshold
`label` is determined by `sentiment_score`:
- `> +0.05` → `"positive"`
- `< −0.05` → `"negative"`
- otherwise → `"neutral"`

---

## Input File Format

Raw news JSON (e.g. from Benzinga/IBKR news feed):

```json
{
  "id": 50186815,
  "headline": "EXCLUSIVE: ENvue Medical, U-Deliver Sign U.S. Distribution Agreement...",
  "summary": "...",
  "symbols": ["FEED"],
  "source": "benzinga"
}
```

Only the `"headline"` field is used. All other fields are ignored.

---

## Relation to FinBERT-analysis.py

| | FinBERT-headliner.py | FinBERT-analysis.py |
|---|---|---|
| Input | Raw news JSON | NER-annotated JSON |
| Text used | `"headline"` field only | All sentences (headline + summary + content) |
| Entity targeting | No | Yes (`[TARGET]` / `[OTHER]` substitution) |
| Output granularity | One score per file | Per-ticker scores across all sentences |
| NER required | No | Yes (run NerSecDicCreator first) |
| Model directory | `./finbert_onnx/` (shared) | `./finbert_onnx/` (shared) |

---

## File Structure

```
FinBERT/
├── FinBERT-headliner.py              # This script
├── FinBERT-analysis.py               # Entity-targeted full-article analysis
├── README_FinBERT-headliner.md       # This file
├── finbert_onnx/                     # Exported ONNX INT8 model (shared)
│   ├── model.onnx
│   ├── config.json
│   ├── tokenizer.json
│   └── ...
└── FEED_28-jan-2026_1.json           # Example raw news input
```
