# API Testing Guide

## Prerequisites

Service must be running:
```bash
python3 pronounCer_service.py
```

## Test Endpoints

### 1. Health Check

**Command**:
```bash
curl http://localhost:5050/health
```

**Expected Response**:
```json
{"status": "healthy"}
```

**What it tests**: Service is running

---

### 2. Get Service Information

**Command**:
```bash
curl http://localhost:5050/
```

**Expected Response**:
```json
{
  "service": "pronounCer Service",
  "version": "2.0",
  "description": "Pronoun and coreference resolution service",
  "current_mode": "simple",
  "fastcoref_available": false,
  "endpoints": {
    "GET /health": "Health check",
    "POST /resolve": "Resolve pronouns/coreferences (body: {'text': 'text to process'})",
    "POST /config": "Configure resolution mode (body: {'mode': 'simple|full'})",
    "GET /": "This information"
  }
}
```

**What it tests**: Service info, available modes, installed features

---

### 3. Switch to Simple Mode

**Command**:
```bash
curl -X POST http://localhost:5050/config \
  -H "Content-Type: application/json" \
  -d '{"mode": "simple"}'
```

**Expected Response**:
```json
{
  "mode": "simple",
  "fastcoref_available": false,
  "status": "success"
}
```

**What it tests**: Mode configuration endpoint

---

### 4. Resolve Pronouns (Simple Mode)

**Command**:
```bash
curl -X POST http://localhost:5050/resolve \
  -H "Content-Type: application/json" \
  -d '{"text": "ENvue Medical announced earnings. It beat expectations."}'
```

**Expected Response**:
```json
{
  "resolved_text": "ENvue Medical announced earnings. ENvue Medical beat expectations.",
  "mode": "simple",
  "status": "success"
}
```

**What it tests**: Simple pronoun resolution (pronouns only)

---

### 5. Resolve Definite Noun Phrase (Simple Mode - Will NOT Work)

**Command**:
```bash
curl -X POST http://localhost:5050/resolve \
  -H "Content-Type: application/json" \
  -d '{"text": "ENvue Medical announced earnings. The company beat expectations."}'
```

**Expected Response** (Simple Mode - No Change):
```json
{
  "resolved_text": "ENvue Medical announced earnings. The company beat expectations.",
  "mode": "simple",
  "status": "success"
}
```

**What it tests**: Limitation of simple mode (noun phrases not resolved)

---

### 6. Try to Switch to Full Mode (Without fastcoref)

**Command**:
```bash
curl -X POST http://localhost:5050/config \
  -H "Content-Type: application/json" \
  -d '{"mode": "full"}'
```

**Expected Response** (If fastcoref not installed):
```json
{
  "mode": "simple",
  "fastcoref_available": false,
  "status": "success"
}
```

**Note**: Mode stays as "simple" because fastcoref is not installed

**What it tests**: Graceful fallback when requested mode unavailable

---

## Testing with fastcoref Installed

If you install fastcoref:
```bash
pip3 install --break-system-packages fastcoref
```

Then restart the service and repeat above tests:

### With fastcoref: Switch to Full Mode

**Command**:
```bash
curl -X POST http://localhost:5050/config \
  -H "Content-Type: application/json" \
  -d '{"mode": "full"}'
```

**Expected Response** (With fastcoref installed):
```json
{
  "mode": "full",
  "fastcoref_available": true,
  "status": "success"
}
```

**What it tests**: Mode switch succeeds when fastcoref available

---

### With fastcoref: Check Service Info

**Command**:
```bash
curl http://localhost:5050/
```

**Expected Response**:
```json
{
  "service": "pronounCer Service",
  "version": "2.0",
  "current_mode": "full",
  "fastcoref_available": true,
  "endpoints": {...}
}
```

**What it tests**: Service correctly reports fastcoref availability and current mode

---

### With fastcoref: Resolve Definite Noun Phrase (Full Mode - Works!)

**Command**:
```bash
curl -X POST http://localhost:5050/resolve \
  -H "Content-Type: application/json" \
  -d '{"text": "ENvue Medical announced earnings. The company beat expectations."}'
```

**Expected Response** (Full Mode With fastcoref):
```json
{
  "resolved_text": "ENvue Medical announced earnings. ENvue Medical beat expectations.",
  "mode": "full",
  "status": "success"
}
```

**What it tests**: Full coreference resolution (pronouns AND noun phrases)

---

## Comparison Test: Simple vs Full

### Setup

**Terminal 1**: Start service
```bash
python3 pronounCer_service.py
```

**Terminal 2**: Run tests
```bash
# Test 1: Simple mode (default)
curl -X POST http://localhost:5050/config \
  -H "Content-Type: application/json" \
  -d '{"mode": "simple"}'

# Test text with pronoun and noun phrase
TEST_TEXT='ENvue Medical, rebranded from NanoVibronix Inc., is a medical technology company specializing in intelligent solutions. It announced earnings. The company beat expectations.'

curl -X POST http://localhost:5050/resolve \
  -H "Content-Type: application/json" \
  -d "{\"text\": \"$TEST_TEXT\"}"
```

### Simple Mode Result
```json
{
  "resolved_text": "ENvue Medical, rebranded from NanoVibronix Inc., is a medical technology company specializing in intelligent solutions. ENvue Medical announced earnings. The company beat expectations.",
  "mode": "simple",
  "status": "success"
}
```

**Analysis**:
- ✅ "It" → "ENvue Medical" (pronoun resolved)
- ❌ "The company" → NOT changed (noun phrase not resolved)

### Full Mode Result (After installing fastcoref)

```bash
# Switch to full mode
curl -X POST http://localhost:5050/config \
  -H "Content-Type: application/json" \
  -d '{"mode": "full"}'

# Same test text
curl -X POST http://localhost:5050/resolve \
  -H "Content-Type: application/json" \
  -d "{\"text\": \"$TEST_TEXT\"}"
```

Expected response:
```json
{
  "resolved_text": "ENvue Medical, rebranded from NanoVibronix Inc., is a medical technology company specializing in intelligent solutions. ENvue Medical announced earnings. ENvue Medical beat expectations.",
  "mode": "full",
  "status": "success"
}
```

**Analysis**:
- ✅ "It" → "ENvue Medical" (pronoun resolved)
- ✅ "The company" → "ENvue Medical" (noun phrase resolved!)

---

## Error Cases

### Missing 'text' field

**Command**:
```bash
curl -X POST http://localhost:5050/resolve \
  -H "Content-Type: application/json" \
  -d '{}'
```

**Expected Response** (400 Bad Request):
```json
{
  "error": "Missing 'text' field in request"
}
```

---

### Invalid mode

**Command**:
```bash
curl -X POST http://localhost:5050/config \
  -H "Content-Type: application/json" \
  -d '{"mode": "invalid"}'
```

**Expected Response** (400 Bad Request):
```json
{
  "error": "mode must be 'simple' or 'full'"
}
```

---

### Missing 'mode' field

**Command**:
```bash
curl -X POST http://localhost:5050/config \
  -H "Content-Type: application/json" \
  -d '{}'
```

**Expected Response** (400 Bad Request):
```json
{
  "error": "Missing 'mode' field in request"
}
```

---

### Empty text

**Command**:
```bash
curl -X POST http://localhost:5050/resolve \
  -H "Content-Type: application/json" \
  -d '{"text": ""}'
```

**Expected Response** (200 OK - empty text handled gracefully):
```json
{
  "resolved_text": "",
  "mode": "simple",
  "status": "success"
}
```

---

## Batch Testing Script

Save as `test_api.sh`:

```bash
#!/bin/bash

SERVICE_URL="http://localhost:5050"

echo "=== Testing pronounCer API ==="
echo ""

echo "1. Health check..."
curl -s "$SERVICE_URL/health" | jq .
echo ""

echo "2. Service info..."
curl -s "$SERVICE_URL/" | jq '.current_mode, .fastcoref_available'
echo ""

echo "3. Configure simple mode..."
curl -s -X POST "$SERVICE_URL/config" \
  -H "Content-Type: application/json" \
  -d '{"mode": "simple"}' | jq '.mode'
echo ""

echo "4. Test simple mode (pronoun)..."
curl -s -X POST "$SERVICE_URL/resolve" \
  -H "Content-Type: application/json" \
  -d '{"text": "Tesla announced. It beat expectations."}' | jq '.resolved_text'
echo ""

echo "5. Test simple mode (noun phrase - NOT resolved)..."
curl -s -X POST "$SERVICE_URL/resolve" \
  -H "Content-Type: application/json" \
  -d '{"text": "Tesla announced. The company beat expectations."}' | jq '.resolved_text'
echo ""

echo "✓ Basic tests complete"
```

Run with:
```bash
chmod +x test_api.sh
./test_api.sh
```

---

## Integration Testing

### Test Service → Client Flow

**Terminal 1**: Start service
```bash
python3 pronounCer_service.py
```

**Terminal 2**: Process files
```bash
# Simple mode (default)
python3 pronounCer.py --inputs FEED_28-jan-2026

# Full mode (if fastcoref installed)
python3 pronounCer.py --inputs FEED_28-jan-2026 --mode full
```

Then verify output files:
```bash
# Check if files were created
ls FEED_28-jan-2026_*_pronouns.txt

# View results
cat FEED_28-jan-2026_content_pronouns.txt
```

---

## Performance Testing

### Measure Simple Mode Performance

```bash
# Configure simple mode
curl -s -X POST http://localhost:5050/config \
  -H "Content-Type: application/json" \
  -d '{"mode": "simple"}' > /dev/null

# Time a test (simple mode)
time curl -s -X POST http://localhost:5050/resolve \
  -H "Content-Type: application/json" \
  -d '{"text": "ENvue Medical announced earnings. It beat expectations."}' > /dev/null
```

Expected: ~50-100ms

### Measure Full Mode Performance (With fastcoref)

```bash
# Configure full mode
curl -s -X POST http://localhost:5050/config \
  -H "Content-Type: application/json" \
  -d '{"mode": "full"}' > /dev/null

# Time a test (full mode)
time curl -s -X POST http://localhost:5050/resolve \
  -H "Content-Type: application/json" \
  -d '{"text": "ENvue Medical announced earnings. The company beat expectations."}' > /dev/null
```

Expected: ~1-2 seconds (first call loads model cache, subsequent calls faster)

---

## Debugging

### Verbose Mode

```bash
# See full response with headers
curl -v http://localhost:5050/health

# Pretty-print JSON responses
curl -s http://localhost:5050/ | jq .

# Save response to file
curl -s -X POST http://localhost:5050/resolve \
  -H "Content-Type: application/json" \
  -d '{"text": "Test text"}' > response.json
cat response.json | jq .
```

### Service Logs

Check service terminal for logs:
```
2026-01-30 13:18:41 - INFO - POST /resolve request received
2026-01-30 13:18:41 - INFO - Text processing complete: 150 chars → 160 chars
2026-01-30 13:18:41 - INFO - Response sent successfully
```

---

## Summary

| Test | Command | Expected Result |
|------|---------|-----------------|
| Health | `curl /health` | `{"status": "healthy"}` |
| Info | `curl /` | Service info with mode |
| Config | `POST /config` | Mode updated |
| Resolve (Simple) | `POST /resolve` text with pronoun | Pronoun replaced |
| Resolve (Noun Phrase) Simple | `POST /resolve` with "the company" | NO change |
| Resolve (Noun Phrase) Full | `POST /resolve` with "the company" | Resolved (with fastcoref) |

All tests documented with exact commands and expected responses.
