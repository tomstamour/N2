import re

with open('pronounCer_service.py', 'r') as f:
    content = f.read()

# Check Part 1: Smarter possessive penalty
print("=" * 60)
print("PART 1: Smarter Possessive Penalty (Lines 242-262)")
print("=" * 60)
if "Smarter possessive penalty: distinguish between descriptive" in content:
    print("✅ Part 1 check: FOUND descriptive vs specific logic")
    if "descriptive_words = " in content and "score -= 10  # Light penalty" in content:
        print("✅ Part 1 check: FOUND reduced penalty (10) for non-descriptive possessives")
    else:
        print("❌ Part 1 check: Missing penalty reduction logic")
else:
    print("❌ Part 1 check: MISSING smarter possessive logic")

# Check Part 2: Entity preservation filter
print("\n" + "=" * 60)
print("PART 2: Entity Preservation Filter (Lines 573-607)")
print("=" * 60)
if "FILTER 6: Protect entity-bearing mentions from generic replacements" in content:
    print("✅ Part 2 check: FOUND FILTER 6 header")
    if "mention_entity_word" in content and "canonical_entity_word" in content:
        print("✅ Part 2 check: FOUND entity word extraction logic")
    if "if mention_entity_word not in canonical_text:" in content:
        print("✅ Part 2 check: FOUND entity preservation check")
    if "ENvue's ENFit Syringes" in content or "BAD - loses" in content:
        print("✅ Part 2 check: FOUND example comments")
else:
    print("❌ Part 2 check: MISSING entity preservation filter")

print("\n" + "=" * 60)
print("VERIFICATION COMPLETE")
print("=" * 60)
