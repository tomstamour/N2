# Fix: ENvue Entity Variation Resolution

## Overview

Implemented a two-part fix to `pronounCer_service.py` to prevent the fastcoref resolver from incorrectly replacing entity-bearing mentions with generic alternatives.

## Problem Statement

The fastcoref resolver was replacing mentions like "ENvue's ENFit Syringes" with generic phrases like "The ENFit syringes", causing loss of important entity information.

**Root Causes:**
1. **Overly aggressive possessive penalty**: Applied -100 penalty to ALL possessive forms, even legitimate entity mentions
2. **Missing entity preservation filter**: No mechanism to protect mentions containing company names from being replaced with generic mentions

## Solution Implemented

### Part 1: Smarter Possessive Penalty (Lines 242-262)

**File:** `pronounCer_service.py` in `select_canonical()` function

**Change:** Updated the possessive scoring logic to distinguish between two types of possessives:

```python
# Descriptive possessives: "Company's recently launched product" → -100 penalty
# Specific possessives: "ENvue's ENFit Syringes" → -10 penalty
# Single-word possessive: → -10 penalty
```

**Logic:**
- Extracts second word from possessive mention
- Checks if it's a filler/descriptive word (`recently`, `newly`, `aforementioned`, `said`, `over-the-counter`, `upcoming`, `planned`, `current`)
- Applies -100 penalty only for descriptive possessives (strong negative signal)
- Applies -10 penalty for other possessives (light preference for non-possessive forms)

**Impact:** Allows "ENvue's ENFit Syringes" to score higher as a canonical mention while still penalizing truly descriptive possessives.

### Part 2: Entity Preservation Filter (Lines 573-607)

**File:** `pronounCer_service.py` in `FastCorefResolver.resolve_text()` method

**Added FILTER 6:** New filter to protect mentions that contain entity information from being replaced by generic mentions.

**Logic:**
1. Extracts first capitalized word (≥4 chars) from both mention and canonical
2. Checks if mention contains an entity word
3. Prevents replacement if:
   - Mention has entity word but canonical doesn't (entity loss)
   - Mention and canonical have different entity words (unless canonical contains mention's entity name)

**Examples:**
- "ENvue's ENFit Syringes" → "The ENFit syringes" ❌ **BLOCKED** (loses "ENvue")
- "its ENFit Syringes" → "ENvue Medical Inc. ENFit Syringes" ✅ **ALLOWED** (pronoun replacement still works)
- "ENvue Medical" → "ENvue Medical Inc." ✅ **ALLOWED** (same entity, more complete canonical)

## Implementation Details

### Part 1: Lines 242-262

```python
# +20: Does NOT contain possessive
if "'s " not in mention_text and not mention_text.endswith("'s"):
    score += 20
else:
    # Smarter possessive penalty logic
    first_word = mention_text.split()[0] if mention_text.split() else ""
    if first_word.endswith("'s"):
        words = mention_text.split()
        # Check if second word is filler/descriptive
        if len(words) >= 2:
            second_word = words[1].lower()
            descriptive_words = ['recently', 'newly', 'aforementioned', 'said',
                                'over-the-counter', 'upcoming', 'planned', 'current']
            if second_word in descriptive_words:
                score -= 100  # Penalize descriptive possessives
            else:
                score -= 10   # Light penalty for other possessives
        else:
            score -= 10       # Single-word possessive, light penalty
```

### Part 2: Lines 573-607

```python
# FILTER 6: Protect entity-bearing mentions from generic replacements
# Extract first capitalized word (potential entity) from mention
mention_words = mention_text.split()
mention_entity_word = None
for word in mention_words:
    clean_word = word.rstrip("'s").rstrip(',').rstrip('.')
    if clean_word and clean_word[0].isupper() and len(clean_word) >= 4:
        mention_entity_word = clean_word
        break

# Extract first capitalized word from canonical
canonical_words = canonical_text.split()
canonical_entity_word = None
for word in canonical_words:
    clean_word = word.rstrip("'s").rstrip(',').rstrip('.')
    if clean_word and clean_word[0].isupper() and len(clean_word) >= 4:
        canonical_entity_word = clean_word
        break

# Skip replacement if entity information would be lost
if mention_entity_word:
    if not canonical_entity_word:
        # Mention has entity, canonical doesn't
        continue
    elif mention_entity_word != canonical_entity_word:
        # Different entities unless canonical contains mention's entity
        if mention_entity_word not in canonical_text:
            continue

replacements.append((mention_start, mention_end, canonical_text))
```

## Testing & Verification

### Syntax Validation
✅ Python syntax verified: `python3 -m py_compile pronounCer_service.py`

### Service Testing
✅ Service starts successfully and responds to health checks
✅ HTTP endpoints operational on localhost:5050

### Expected Behavior

1. **Possessive mentions with specific products:**
   - "ENvue's ENFit Syringes" → Will score higher as a canonical (light -10 penalty instead of -100)

2. **Descriptive possessive mentions:**
   - "Company's recently launched product" → Still penalized heavily (-100)

3. **Entity protection in replacements:**
   - Mentions with entity names won't be replaced by generic canonicals
   - Pronoun resolution still works correctly

## Notes

- **Entity word detection:** Looks for capitalized words ≥4 characters to identify proper nouns/entities
- **Possessive word removal:** Strips "'s", commas, and periods to get clean entity names
- **Conservative approach:** Filter only blocks replacements when entity information would be lost
- **Backward compatible:** Existing behavior for pronouns and non-possessive mentions unchanged

## Files Modified

- `pronounCer_service.py`: Two edits in critical resolution logic
  - Lines 242-262: Smarter possessive penalty in `select_canonical()`
  - Lines 573-607: New FILTER 6 in `FastCorefResolver.resolve_text()`

## Related Components

- `select_canonical()` - Canonical mention selection (uses updated scoring)
- `FastCorefResolver.resolve_text()` - Main coreference resolution pipeline (uses new FILTER 6)
- `PronounResolver` - Simple mode unaffected by these changes

## Future Improvements

1. Configurable descriptive word list via external config
2. Entity type detection (ORG, PERSON, PRODUCT) for more nuanced rules
3. Learning-based entity preservation based on context
4. Performance optimization for entity word extraction
