# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

ML training data directory. Prepares datasets for XGBoost models predicting market outcomes from PR sentiment and technical features. Read parent `../CLAUDE.md` first for venv and git rules.

⚠ **Planning model-3? Read `model-3_latency_training_guide.md` FIRST.** Since 2026-09-20
the leg is on the trade path under a 2-second deadline enforced in `../x-wing-mole/`, so
**runtime is now a training constraint, not just accuracy**. That guide carries the
measured per-model cost table, the two quantisation dead ends (INT8 destroys both FP32
models — the embedder and DeBERTa), and the decisions that can **only** be made at a
generation boundary (the 200-word body cap, the PCA basis).

## Interpreter

Use `/home/tom/venv/bin/python` for all scripts.

## Data Pipeline

File naming: `08_YYYY-MM-DD-YYYY-MM-DD_RawData.tsv` → `_splits.tsv` → `_splits_rmCol.tsv` (current).

Run column removal:
```bash
/home/tom/venv/bin/python columns_remover.py \
  --input <INPUT_TSV> --column-to-remove-file columns_to_delete.txt
```

`columns_to_delete.txt` lists metadata columns to drop before train/validation split.

## Dataset Structure

~200 columns:
- **PR metadata**: Symbol, ArrivalTime, Headline, Author, Created, Body
- **Market**: Float, MktCap, Exchange, ArrivalDate
- **Target**: `fromPR_High(%)` — % gain from PR arrival to intraday high
- **Sentiment encoders**: FinMarBa, FLANG, RoBERTa, DeBERTa, FinBERT-Tone, FinancialBERT, DistilRoBERTa, Trading-Hero, LouMcD (headline `_hd` + body `_bd` variants)
- **Classifiers**: FineClass, BroadClass, kw15_proba

## Model Architecture

XGBoost takes two feature streams:

1. **Structured features** → direct to XGBoost (Float, MktCap, ATR, RSI, SI, hour, day-of-week, sentiment scores)
2. **Embeddings** (768-dim FinBERT) → PCA (20-30 components, ~95% variance) → XGBoost

**Critical**: Fit PCA **only on training fold**, apply fixed transform to validation/test. Store raw embeddings separately for re-tuning without re-running FinBERT.

## Train/Validation Split

Split by date (~70% train, ~30% validation). Fit all transforms on train only. `fromPR_High(%)` is naturally forward-looking; no artificial leakage.

## Key Files

- `08_2026-05-04-2026-08-28_RawData_splits_rmCol.tsv` — main training dataset
- `columns_remover.py` — filter TSV columns
- `columns_to_delete.txt` — columns to remove (one per line)
- `documentation/` — ML design notes

## Inference

Save with model: (1) PCA transform (pickle), (2) XGBoost model, (3) feature metadata (JSON). Use same transform chain at inference.

## Gotchas

- Do NOT fit PCA on full dataset then split (walk-forward leakage)
- Do NOT run PCA on structured features (float, RSI, etc.) — already interpretable and low-dim
- Tree models handle feature correlation natively; no pre-filtering needed
