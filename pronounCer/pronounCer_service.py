#!/usr/bin/env python3
"""
pronounCer Service - Persistent Background Service for Coreference Resolution

This service keeps the spaCy NLP model loaded in memory and performs
pronoun/coreference analysis via HTTP requests. Uses spaCy's built-in
NER and syntactic parsing to identify and resolve pronoun references.

Usage:
    python3 pronounCer_service.py

The service will listen on http://localhost:5050

Endpoints:
    GET  /health - Health check (returns 200)
    POST /resolve - Analyze pronouns and coreferences in text
        Request body: {"text": "text to process"}
        Response: {"resolved_text": "analyzed text", "status": "success"}
"""

import json
import logging
import re
import sys
from flask import Flask, request, jsonify
from pathlib import Path

try:
    import spacy
except ImportError as e:
    print(f"Error: Required package not installed: {e}")
    print("\nInstall with:")
    print("  pip3 install --break-system-packages spacy flask requests")
    print("  python3 -m spacy download en_core_web_sm")
    sys.exit(1)

# Optional: Try to import fastcoref for full coreference resolution
try:
    from fastcoref import FCoref
    FASTCOREF_AVAILABLE = True
except ImportError:
    FASTCOREF_AVAILABLE = False
    FCoref = None

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Flask app
app = Flask(__name__)

# Global variables
nlp = None  # spaCy model
resolver = None  # Current resolver (PronounResolver or FastCorefResolver)
resolver_mode = "simple"  # "simple" or "full"


class PronounResolver:
    """Simple pronoun resolution using spaCy and pattern matching."""

    def __init__(self, nlp_model):
        """Initialize resolver with spaCy model."""
        self.nlp = nlp_model
        self.common_pronouns = {
            'he', 'she', 'it', 'they', 'we', 'you',
            'his', 'her', 'its', 'their', 'our', 'your',
            'him', 'them', 'us',
            'this', 'that', 'these', 'those'
        }

    def resolve_text(self, text):
        """
        Resolve pronouns in text by replacing them with their antecedents.

        This approach:
        1. Parses text with spaCy to identify entities
        2. Finds pronouns and their likely antecedents
        3. Replaces pronouns with their antecedent text
        4. Replaces corporate noun phrases ("the company") with the main entity
        5. Skips first-person pronouns inside quoted regions (they refer to the speaker)

        Uses token-based reconstruction to respect word boundaries
        and avoid substring matching issues.
        """
        if not text.strip():
            return text

        doc = self.nlp(text)

        # --- Pre-compute context used by the three fix passes ---

        # Most-frequent non-generic ORG entity — the "main" entity.
        # Frequency beats document order: the issuer dominates in press releases
        # while dateline boilerplate ("GLOBE NEWSWIRE") appears only once.
        # Single-word generic nouns ("Company", "Group") are excluded even when
        # spaCy tags them as ORG due to capitalisation.
        _org_freq: dict = {}
        for ent in doc.ents:
            if (ent.label_ == "ORG"
                    and ent.text.lower() not in _GENERIC_CORPORATE_NOUNS):
                _org_freq[ent.text] = _org_freq.get(ent.text, 0) + 1
        main_entity_text = max(_org_freq, key=_org_freq.get) if _org_freq else None

        # Character spans of double-quoted regions.  First-person pronouns
        # inside these represent the speaker, not the company (Bug C).
        # Handles both closed ("…") and unclosed ("… at end-of-text) quotes.
        quote_ranges = []
        in_quote = False
        quote_start = 0
        for i, ch in enumerate(text):
            if ch == '\u201c' or (ch == '"' and not in_quote):
                quote_start = i
                in_quote = True
            elif ch == '\u201d' or (ch == '"' and in_quote):
                quote_ranges.append((quote_start, i + 1))
                in_quote = False
        if in_quote:
            quote_ranges.append((quote_start, len(text)))

        first_person = {'we', 'our', 'us'}

        # Build a mapping of token index -> replacement text
        replacements = {}
        # Tokens to suppress entirely in output (second token of a corporate phrase)
        skip_tokens = set()

        # --- PRON loop (Bug B: pass main_entity_text; Bug C: skip first-person in quotes) ---
        for token in doc:
            if token.pos_ == "PRON" and token.text.lower() in self.common_pronouns:
                # Bug C: first-person pronouns inside quotes are the speaker — leave them
                if token.text.lower() in first_person:
                    if any(start <= token.idx < end for start, end in quote_ranges):
                        continue

                antecedent = self._find_nearest_noun(doc, token, main_entity_text=main_entity_text)
                if antecedent:
                    replacements[token.i] = antecedent

        # --- Corporate-phrase loop (Bug A: "The company" → main entity) ---
        corporate_nouns = {
            'company', 'firm', 'corporation', 'organization',
            'business', 'maker', 'unit', 'parent', 'group'
        }
        if main_entity_text is not None:
            for token in doc:
                if (token.pos_ == "DET"
                        and token.text.lower() == "the"
                        and token.i + 1 < len(doc)):
                    next_token = doc[token.i + 1]
                    if (next_token.pos_ in ("NOUN", "PROPN")
                            and next_token.lemma_.lower() in corporate_nouns):
                        replacements[token.i] = main_entity_text
                        skip_tokens.add(next_token.i)
                        # Drop possessive marker: "the Company's" → entity
                        if (token.i + 2 < len(doc)
                                and doc[token.i + 2].text in ("'s", "’s")):
                            skip_tokens.add(token.i + 2)

        # Reconstruct text with replacements, respecting token boundaries
        result_parts = []
        for token in doc:
            if token.i in skip_tokens:
                # Second token of a corporate phrase — already consumed by the
                # replacement on the DET token; emit nothing.
                continue
            if token.i in replacements:
                result_parts.append(replacements[token.i])
                result_parts.append(token.whitespace_)
            else:
                result_parts.append(token.text_with_ws)

        return ''.join(result_parts)

    def _find_nearest_noun(self, doc, pronoun_token, main_entity_text=None):
        """
        Find the best antecedent for a pronoun.
        Prioritizes: 1) Named entities, 2) Subject nouns, 3) Substantial nouns

        When main_entity_text is provided the backward scan will not stop early
        on a high-scoring entity that is NOT the main entity — it keeps scanning
        so that the main entity (further back in the doc) can still win.
        """
        # Words to skip (temporal, location markers that are poor antecedents)
        skip_words = {
            'monday', 'tuesday', 'wednesday', 'thursday', 'friday',
            'saturday', 'sunday', 'today', 'yesterday', 'tomorrow',
            'january', 'february', 'march', 'april', 'may', 'june',
            'july', 'august', 'september', 'october', 'november', 'december'
        }

        best_entity = None
        best_score = 0

        # Look backward from pronoun position
        for i in range(pronoun_token.i - 1, -1, -1):
            token = doc[i]

            # Skip unsuitable tokens
            if token.text.lower() in skip_words or len(token.text) <= 1:
                continue

            if token.pos_ in ("NOUN", "PROPN"):
                score = 0
                entity_text = token.text

                # Check if part of a named entity (highest priority)
                for ent in doc.ents:
                    if token.i >= ent.start and token.i < ent.end:
                        entity_text = ent.text
                        # Prioritize certain entity types
                        if ent.label_ in ("ORG", "PERSON", "PRODUCT", "GPE"):
                            score = 100
                        else:
                            score = 80
                        break

                # If not in entity, check if it's a subject (high priority)
                if score == 0 and token.dep_ in ("nsubj", "nsubjpass"):
                    score = 60
                # Regular proper noun
                elif score == 0 and token.pos_ == "PROPN":
                    score = 40
                # Common noun (lowest priority)
                elif score == 0:
                    score = 20

                # Take the first high-scoring match
                if score > best_score:
                    best_entity = entity_text
                    best_score = score

                # When a main entity is being tracked and we just landed on it
                # (or a prefix variation like "ENvue Medical" vs "ENvue Medical Inc."),
                # return the canonical form immediately — don't let a closer
                # subsidiary entity win on the score tie.
                if main_entity_text is not None and score >= 80:
                    if (entity_text == main_entity_text
                            or main_entity_text.startswith(entity_text)
                            or entity_text.startswith(main_entity_text)):
                        return main_entity_text

                # No main entity to hunt for — first strong match is good enough.
                if best_score >= 80 and main_entity_text is None:
                    break

        return best_entity


_GENERIC_CORPORATE_NOUNS = {
    'company', 'firm', 'corporation', 'organization', 'organisation',
    'business', 'maker', 'unit', 'parent', 'group', 'entity', 'issuer',
}
_GENERIC_STOPWORDS = {'the', 'a', 'an', 'this', 'that', 'these', 'those'}
_COMPANY_SUFFIXES = (
    ' Inc.', ' Inc', ' Corp.', ' Corp', ' Corporation',
    ' Ltd.', ' Ltd', ' Limited',
    ' LLC', ' L.L.C.', ' Co.', ' Company',
    ' Holdings', ' Group', ' PLC', ' plc', ' AG', ' SA', ' N.V.',
)


def is_purely_generic_corporate(mention_text):
    """True if mention is just determiner(s) + generic corporate noun(s).

    Examples that return True: "The Company", "the firm", "this corporation",
    "The Company's", "the company’s".
    """
    content_words = []
    for w in mention_text.split():
        w_clean = w.lower().strip(".,'\"“”‘’")
        if w_clean.endswith("'s") or w_clean.endswith("’s"):
            w_clean = w_clean[:-2]
        if w_clean and w_clean not in _GENERIC_STOPWORDS:
            content_words.append(w_clean)
    return bool(content_words) and all(w in _GENERIC_CORPORATE_NOUNS for w in content_words)


def find_unclustered_generic_phrases(text, global_main_entity, existing_replacements):
    """Catch purely-generic phrases ("The Company", "the Corporation", ...)
    that fastcoref left unclustered (singleton clusters are skipped upstream)
    and that no other pass has replaced. Returns (start, end, repl) tuples.

    Skips matches that overlap an existing replacement, possessive forms
    (owned by the cluster pass), and quoted/parenthetical definitions like
    `the "Company"` or `("Company")`.
    """
    if not global_main_entity:
        return []

    occupied = set()
    for s, e, _ in existing_replacements:
        occupied.update(range(s, e))

    nouns = '|'.join(re.escape(n.capitalize()) for n in _GENERIC_CORPORATE_NOUNS)
    pattern = re.compile(rf"\b[Tt]he\s+(?:{nouns})\b(?![’']s)")

    out = []
    for m in pattern.finditer(text):
        start, end = m.span()
        if any(i in occupied for i in range(start, end)):
            continue
        # Skip the literal definition form: ` the "Company" `, ` ("Company") `
        left_ctx = text[max(0, start - 2):start + 4]
        right_ctx = text[end:end + 1]
        if any(q in left_ctx for q in ('"', '“', '‘', '(')) and \
           any(q in right_ctx for q in ('"', '”', '’', ')')):
            continue
        out.append((start, end, global_main_entity))

    return out


def remove_overlapping_replacements(replacements):
    """
    Remove overlapping replacements, keeping the one with longer span.

    When fastcoref creates multiple clusters with overlapping mentions,
    this function detects and removes conflicts. For any overlapping
    replacements, the one with the longer character span is kept.

    Args:
        replacements: List of (start, end, replacement_text) tuples

    Returns:
        Filtered list with overlapping replacements removed
    """
    if len(replacements) <= 1:
        return replacements

    # Sort by start position, then by span length (descending)
    # This ensures we process mentions by their position and prioritize longer spans
    sorted_reps = sorted(replacements, key=lambda x: (x[0], -(x[1] - x[0])))

    filtered = []
    for start, end, text in sorted_reps:
        # Check if this overlaps with any already-accepted replacement
        overlaps = False
        for f_start, f_end, _ in filtered:
            # Two spans overlap if one starts before the other ends
            if (start < f_end and end > f_start):
                overlaps = True
                break

        if not overlaps:
            filtered.append((start, end, text))

    return filtered


def select_canonical(cluster, text):
    """
    Choose the best canonical mention from a cluster.

    Strategy:
    - Prefer named entities and proper nouns
    - Avoid long descriptive phrases
    - Avoid possessive forms as canonical
    - Prefer shorter, entity-like mentions

    Args:
        cluster: List of (start, end) tuples representing mentions
        text: Original text string

    Returns:
        (start, end) tuple of the best canonical mention
    """
    best_score = -float('inf')
    best_mention = cluster[0]  # Fallback to first mention

    for start, end in cluster:
        mention_text = text[start:end]
        score = 0

        # +50: Starts with capital letter (likely proper noun)
        if mention_text and mention_text[0].isupper():
            score += 50

        # +30: Contains company indicators
        if any(indicator in mention_text for indicator in _COMPANY_SUFFIXES):
            score += 30

        # +20: Does NOT contain possessive
        if "'s " not in mention_text and not mention_text.endswith("'s"):
            score += 20
        else:
            # Smarter possessive penalty: distinguish between descriptive and specific possessives
            # "Company's recently launched product" → penalize (descriptive)
            # "ENvue's ENFit Syringes" → light penalty (specific product name)
            first_word = mention_text.split()[0] if mention_text.split() else ""
            if first_word.endswith("'s"):
                words = mention_text.split()
                # Check if second word is a filler/descriptive word
                if len(words) >= 2:
                    second_word = words[1].lower()
                    descriptive_words = ['recently', 'newly', 'aforementioned', 'said',
                                        'over-the-counter', 'upcoming', 'planned', 'current']
                    if second_word in descriptive_words:
                        score -= 100  # Penalize descriptive possessives
                    else:
                        score -= 10  # Light penalty for other possessives (prefer non-possessive forms)
                else:
                    score -= 10  # Single-word possessive, light penalty

        # Penalize filler/descriptive words
        filler_words = ['the', 'this', 'that', 'these', 'those', 'recently', 'launched',
                       'newly', 'over-the-counter', 'aforementioned', 'said']
        words = mention_text.lower().split()
        filler_count = sum(1 for word in words if word in filler_words)
        score -= (filler_count * 15)  # -15 points per filler word

        # Prefer shorter mentions (but not too short).
        # Mild scaling so well-formed company names (~40 chars) don't lose
        # to short generic phrases like "The Company".
        length = len(mention_text)
        if length < 5:
            score -= 50
        elif length <= 40:
            score -= length // 2
        else:
            score -= 20 + length

        # Strong penalty: mentions whose only content words are generic
        # corporate nouns ("The Company", "the firm", "this corporation")
        # should never beat a real proper-name mention.
        if is_purely_generic_corporate(mention_text):
            score -= 80

        if score > best_score:
            best_score = score
            best_mention = (start, end)

    return best_mention


def build_entity_variation_map(canonical_entities):
    """
    Build map of entity name variations to canonical forms.

    For "ENvue Medical Inc.", creates mapping:
      "ENvue Medical" -> "ENvue Medical Inc."
      "ENvue" -> "ENvue Medical Inc." (only if no ambiguity)

    Args:
        canonical_entities: Dict of {canonical_text: (start, end)}

    Returns:
        Dict mapping {variation: canonical_text}
    """
    variations = {}

    for canonical in canonical_entities.keys():
        # Strategy 1: Remove company suffixes to create partial variation
        # "Company Name Inc." -> "Company Name"
        company_suffixes = [' Inc.', ' Corp.', ' Ltd.', ' LLC', ' Co.']
        for suffix in company_suffixes:
            if canonical.endswith(suffix):
                partial = canonical.replace(suffix, '')
                if len(partial) >= 5:  # Avoid too-short partials like "ABC"
                    variations[partial] = canonical
                break

        # Strategy 2: Extract first word if it's a distinctive name
        # "ENvue Medical Inc." -> "ENvue" (only if unique)
        first_word = canonical.split()[0]
        if len(first_word) >= 5 and first_word[0].isupper():
            # Only add if no other canonical starts with same word (avoid ambiguity)
            conflicts = sum(1 for c in canonical_entities.keys() if c.startswith(first_word))
            if conflicts == 1:
                variations[first_word] = canonical

    return variations


def find_missed_entity_variations(text, entity_variations, existing_replacements, clusters):
    """
    Scan text for entity variations that fastcoref missed.

    Uses regex pattern matching to find potential entity mentions,
    checks if they match known variations, and creates replacements
    if not already covered by fastcoref clusters.

    Args:
        text: Original text
        entity_variations: Map of {partial_entity: canonical_entity}
        existing_replacements: List of (start, end, repl) tuples already planned
        clusters: Original fastcoref clusters (to check if mention is already clustered)

    Returns:
        List of (start, end, canonical_text) tuples for missed variations
    """
    additional = []

    # Build set of character ranges already covered by clusters
    clustered_ranges = set()
    for cluster in clusters:
        for start, end in cluster:
            for i in range(start, end):
                clustered_ranges.add(i)

    # Build set of character ranges already being replaced
    replacement_ranges = set()
    for start, end, _ in existing_replacements:
        for i in range(start, end):
            replacement_ranges.add(i)

    # Search for each entity variation in the text
    for variation, canonical in entity_variations.items():
        # Use word boundary regex to find exact matches
        pattern = r'\b' + re.escape(variation) + r'\b'

        for match in re.finditer(pattern, text):
            start, end = match.span()

            # Skip if this range is already covered by a cluster
            if any(i in clustered_ranges for i in range(start, end)):
                continue

            # Skip if this range is already being replaced
            if any(i in replacement_ranges for i in range(start, end)):
                continue

            # Skip if the source text at this span already reads the full canonical
            # (the variation is a prefix of the canonical that's already present).
            # e.g. "Dreamland" at a position where text reads "Dreamland Limited"
            # should not be expanded to "Dreamland Limited Limited".
            if text[start:start + len(canonical)] == canonical:
                continue

            # Add replacement for this missed variation
            additional.append((start, end, canonical))

            # Mark this range as covered
            for i in range(start, end):
                replacement_ranges.add(i)

    return additional


def find_pronouns_in_quotes(text, canonical_entities, existing_replacements, clusters):
    """
    Find pronouns in quotes that refer to entities but weren't clustered.

    Looks for pronouns (we, our, we're) inside quoted text and checks if
    they're near an entity mention. If so, creates replacement using that
    entity's canonical form.

    Args:
        text: Original text
        canonical_entities: Map of {canonical_text: (start, end)}
        existing_replacements: List of (start, end, repl) tuples already planned
        clusters: Original fastcoref clusters (to check if pronoun is already clustered)

    Returns:
        List of (start, end, canonical_text) tuples for pronouns in quotes
    """
    additional = []

    # Build set of character ranges already covered by clusters
    clustered_ranges = set()
    for cluster in clusters:
        for start, end in cluster:
            for i in range(start, end):
                clustered_ranges.add(i)

    # Build set of character ranges already being replaced
    replacement_ranges = set()
    for start, end, _ in existing_replacements:
        for i in range(start, end):
            replacement_ranges.add(i)

    # Find all quoted sections
    quote_pattern = r'"([^"]+)"'
    for quote_match in re.finditer(quote_pattern, text):
        quote_start, quote_end = quote_match.span()
        quote_text = quote_match.group(1)

        # Find pronouns in this quote
        pronoun_patterns = [
            r"\bWe're\b", r"\bWe\b", r"\bOur\b", r"\bUs\b",
            r"\bwe're\b", r"\bwe\b", r"\bour\b", r"\bus\b"
        ]

        for pattern in pronoun_patterns:
            for pronoun_match in re.finditer(pattern, quote_text):
                # Calculate absolute position in text
                pronoun_start = quote_start + 1 + pronoun_match.start()
                pronoun_end = quote_start + 1 + pronoun_match.end()

                # Skip if already clustered
                if any(i in clustered_ranges for i in range(pronoun_start, pronoun_end)):
                    continue

                # Skip if already being replaced
                if any(i in replacement_ranges for i in range(pronoun_start, pronoun_end)):
                    continue

                # Find nearest entity mention before this quote
                # Look backwards from quote_start to find an entity
                context_window = 200  # chars to search backward
                context_start = max(0, quote_start - context_window)

                # Find which canonical entity appears in the context
                nearest_entity = None
                nearest_distance = float('inf')

                for canonical_text, (ent_start, ent_end) in canonical_entities.items():
                    # Check if this entity appears in the context window
                    if context_start <= ent_start < quote_start:
                        distance = quote_start - ent_end
                        if distance < nearest_distance:
                            nearest_distance = distance
                            nearest_entity = canonical_text

                # If we found a nearby entity, use it as the replacement
                if nearest_entity:
                    additional.append((pronoun_start, pronoun_end, nearest_entity))

                    # Mark this range as covered
                    for i in range(pronoun_start, pronoun_end):
                        replacement_ranges.add(i)

    return additional


class FastCorefResolver:
    """Full coreference resolution using fastcoref."""

    def __init__(self, device: str = None):
        """Initialize fastcoref model.

        Args:
            device: 'cpu', 'cuda', 'cuda:0', etc. If None, auto-detect:
                use CUDA when available, otherwise CPU. Falls back silently
                to CPU if torch is not importable.
        """
        if not FASTCOREF_AVAILABLE:
            raise RuntimeError("fastcoref is not installed")

        if device is None:
            try:
                import torch
                device = 'cuda' if torch.cuda.is_available() else 'cpu'
            except Exception:
                device = 'cpu'

        logger.info(f"Loading fastcoref model (device={device})...")
        try:
            self.model = FCoref(device=device)
        except TypeError:
            # Older fastcoref versions did not accept device kwarg.
            logger.warning("FCoref(device=…) not supported by this fastcoref version; using default device")
            self.model = FCoref()
        self.device = device
        logger.info(f"fastcoref model loaded successfully (device={device})")

    def resolve_text(self, text):
        """
        Resolve ALL coreferences (pronouns + definite noun phrases).
        Uses character positions to avoid cascading replacement bugs.

        Returns text with coreferences replaced by their antecedents.
        """
        if not text.strip():
            return text

        try:
            # Get predictions from fastcoref
            preds = self.model.predict(texts=[text])

            # Get clusters with CHARACTER POSITIONS instead of strings
            clusters = preds[0].get_clusters(as_strings=False)

            # DEBUG: Log detected clusters (verbose; demoted to DEBUG so the
            # WebSocket hot path doesn't pay for I/O on every article)
            logger.debug(f"=== Detected {len(clusters)} coreference clusters ===")
            for i, cluster in enumerate(clusters):
                cluster_texts = [text[start:end] for start, end in cluster]
                logger.debug(f"Cluster {i} ({len(cluster)} mentions):")
                for j, (mention_text, (start, end)) in enumerate(zip(cluster_texts, cluster)):
                    if i == 0 or j < 8:
                        logger.debug(f"  [{j}] ({start}-{end}): '{mention_text}'")
                if i > 0 and len(cluster) > 8:
                    logger.debug(f"  ... and {len(cluster) - 8} more mentions")

            if not clusters:
                return text  # No coreferences found

            # Find a "global main entity" across ALL clusters: a capitalized
            # mention that includes a company suffix and is not purely generic.
            # Prefer mentions without noise (parens, quotes) and the shortest
            # such mention — fastcoref often emits long spans like
            # 'X Inc. (NYSE: X, or the "Company")' which we don't want as the
            # canonical replacement.
            _NOISE_CHARS = ('(', ')', '"', '“', '”', '‘', '’')
            global_candidates = []
            for cluster in clusters:
                for m_start, m_end in cluster:
                    mention = text[m_start:m_end].strip()
                    if not mention or not mention[0].isupper():
                        continue
                    if not any(s in mention for s in _COMPANY_SUFFIXES):
                        continue
                    if is_purely_generic_corporate(mention):
                        continue
                    global_candidates.append(mention)
            if global_candidates:
                global_main_entity = min(
                    global_candidates,
                    key=lambda m: (any(c in m for c in _NOISE_CHARS), len(m)),
                )
                logger.debug(f"=== Global main entity: '{global_main_entity}' ===")
            else:
                global_main_entity = None

            # Build list of (start, end, replacement_text) tuples
            replacements = []

            for i, cluster in enumerate(clusters):
                if len(cluster) <= 1:
                    continue  # Single-mention cluster, nothing to replace

                # Choose best canonical mention using smart selection
                canonical_span = select_canonical(cluster, text)
                canonical_start, canonical_end = canonical_span
                canonical_text = text[canonical_start:canonical_end]

                # If the chosen canonical is purely generic ("The Company"),
                # swap it for the article's global main entity. The original
                # canonical span is kept for FILTER 1 (overlap protection).
                if global_main_entity and is_purely_generic_corporate(canonical_text):
                    logger.debug(f"  Swapping generic canonical '{canonical_text}' -> '{global_main_entity}'")
                    canonical_text = global_main_entity

                # DEBUG: Log canonical selection
                logger.debug(f"  Canonical selected: '{canonical_text}' (score-based selection from {len(cluster)} mentions)")


                for mention_span in cluster[1:]:
                    mention_start, mention_end = mention_span
                    mention_text = text[mention_start:mention_end]

                    # FILTER 1: Skip if mention overlaps with canonical position
                    # Prevents replacing "Inc." within "ENvue Medical Inc."
                    if (mention_start >= canonical_start and mention_start < canonical_end) or \
                       (mention_end > canonical_start and mention_end <= canonical_end):
                        continue

                    # FILTER 2: Skip overly generic single words
                    # Prevents "The" from being replaced with full company names
                    if len(mention_text) <= 2 and mention_text.lower() in {'it', 'he', 'we', 'i', 'the', 'a'}:
                        continue

                    # FILTER 3: Skip if mention text is a problematic substring of canonical
                    # Prevents "Medical Inc." → "ENvue Medical Inc." creating duplication
                    # BUT: Allow "ENvue Medical" → "ENvue Medical Inc." (legitimate partial entity)
                    # Heuristic: Skip only if mention is a SUFFIX of canonical or clearly a fragment
                    # (not starting with the same first word as canonical)
                    if mention_text in canonical_text:
                        # If the source text at this span already reads the full canonical,
                        # substituting would duplicate the remainder — e.g. "Dreamland" →
                        # "Dreamland Limited" inside "Dreamland Limited" yields
                        # "Dreamland Limited Limited".
                        if text[mention_start:mention_start + len(canonical_text)] == canonical_text:
                            continue

                        # Check if mention and canonical start with same word (e.g., "ENvue Medical" vs "ENvue Medical Inc.")
                        mention_first_word = mention_text.split()[0] if mention_text.split() else ""
                        canonical_first_word = canonical_text.split()[0] if canonical_text.split() else ""

                        # Only skip if they DON'T start with same word (e.g., "Medical Inc." is a bad substring)
                        # OR if mention is very short (< 5 chars, likely a single word fragment)
                        if mention_first_word != canonical_first_word or len(mention_text) < 5:
                            continue

                    # FILTER 4: Skip if canonical is substring of mention
                    # Prevents expanding "ENvue Medical Inc." to longer phrases
                    if canonical_text in mention_text:
                        continue

                    # FILTER 5: Skip if replacement would significantly expand mention length
                    # Prevents "The over-the-counter ENFit Syringes" (35 chars) → long phrase (79 chars)
                    # Allow pronouns to expand (e.g., "its" → "ENvue Medical Inc.")
                    # Block longer mentions from expanding beyond 2x their length
                    if len(mention_text) >= 15 and len(canonical_text) > len(mention_text) * 2:
                        continue

                    # FILTER 6: Protect entity-bearing mentions from generic replacements
                    # Don't replace mentions containing entity names with canonicals that lack them
                    # Example: "ENvue's ENFit Syringes" → "The ENFit syringes" (BAD - loses "ENvue")
                    #          "its ENFit Syringes" → "ENvue Medical Inc. ENFit Syringes" (GOOD)

                    # Extract first capitalized word from mention (potential entity name)
                    mention_words = mention_text.split()
                    mention_entity_word = None
                    for word in mention_words:
                        # Check if word starts with capital and is substantial (≥4 chars)
                        # Strip ASCII and smart-quote possessive forms.
                        clean_word = word.rstrip("’s'").rstrip(',').rstrip('.')
                        if clean_word and clean_word[0].isupper() and len(clean_word) >= 4:
                            mention_entity_word = clean_word
                            break

                    # If the mention's "entity word" is actually a generic corporate
                    # noun (Company, Corporation, Firm, ...), it is not a real entity
                    # worth protecting — let the replacement proceed.
                    if mention_entity_word and mention_entity_word.lower() in _GENERIC_CORPORATE_NOUNS:
                        mention_entity_word = None

                    # Extract first capitalized word from canonical
                    canonical_words = canonical_text.split()
                    canonical_entity_word = None
                    for word in canonical_words:
                        clean_word = word.rstrip("’s'").rstrip(',').rstrip('.')
                        if clean_word and clean_word[0].isupper() and len(clean_word) >= 4:
                            canonical_entity_word = clean_word
                            break

                    # Skip replacement if mention has entity word but canonical doesn't
                    # OR if they have different entity words (e.g., "ENvue" vs "Medical")
                    if mention_entity_word:
                        if not canonical_entity_word:
                            # Mention has entity, canonical doesn't → skip
                            continue
                        elif mention_entity_word != canonical_entity_word:
                            # Different entities → skip (unless canonical is more complete)
                            # Example: "ENvue" in mention, "ENvue" also in canonical → allow
                            if mention_entity_word not in canonical_text:
                                continue

                    replacements.append((mention_start, mention_end, canonical_text))

            # DEBUG: Log replacements before overlap removal
            logger.debug(f"Replacements before overlap removal ({len(replacements)} total):")
            for i, (start, end, repl) in enumerate(replacements[:10]):  # First 10
                logger.debug(f"  [{i}] ({start}-{end}): '{text[start:end]}' → '{repl}'")
            if len(replacements) > 10:
                logger.debug(f"  ... and {len(replacements) - 10} more replacements")

            # Remove overlapping replacements from different clusters
            # Keeps longer span when two replacements overlap
            replacements = remove_overlapping_replacements(replacements)

            # DEBUG: Log replacements after overlap removal
            logger.debug(f"Replacements after overlap removal ({len(replacements)} total):")
            for i, (start, end, repl) in enumerate(replacements[:10]):  # First 10
                logger.debug(f"  [{i}] ({start}-{end}): '{text[start:end]}' → '{repl}'")
            if len(replacements) > 10:
                logger.debug(f"  ... and {len(replacements) - 10} more replacements")

            # ========== PHASE: ENTITY VARIATION NORMALIZATION ==========
            # DEBUG: Log pre-normalization count
            logger.debug(f"Replacements before entity normalization: {len(replacements)}")

            # Step 1: Extract canonical entities from all clusters
            canonical_entities = {}
            for cluster in clusters:
                if len(cluster) <= 1:
                    continue
                canonical_span = select_canonical(cluster, text)
                canonical_start, canonical_end = canonical_span
                canonical_text = text[canonical_start:canonical_end]
                canonical_entities[canonical_text] = canonical_span

            # Step 2: Build entity variation map (partial names -> canonical)
            entity_variations = build_entity_variation_map(canonical_entities)

            # DEBUG: Log entity variation map
            if entity_variations:
                logger.debug(f"Entity variation map ({len(entity_variations)} variations):")
                for variation, canonical in entity_variations.items():
                    logger.debug(f"  '{variation}' -> '{canonical}'")

            # Step 3: Find missed entity variations not in any cluster
            additional_replacements = find_missed_entity_variations(
                text,
                entity_variations,
                existing_replacements=replacements,
                clusters=clusters
            )

            # DEBUG: Log additional replacements found
            if additional_replacements:
                logger.debug(f"Found {len(additional_replacements)} missed entity variations:")
                for start, end, repl in additional_replacements:
                    logger.debug(f"  ({start}-{end}): '{text[start:end]}' -> '{repl}'")

            # Step 4: Merge entity variation replacements
            replacements.extend(additional_replacements)

            # Step 5: Find pronouns in quotes that refer to entities
            pronoun_replacements = find_pronouns_in_quotes(
                text,
                canonical_entities,
                existing_replacements=replacements,
                clusters=clusters
            )

            # DEBUG: Log pronoun replacements found
            if pronoun_replacements:
                logger.debug(f"Found {len(pronoun_replacements)} pronouns in quotes:")
                for start, end, repl in pronoun_replacements:
                    logger.debug(f"  ({start}-{end}): '{text[start:end]}' -> '{repl}'")

            # Step 6: Merge pronoun replacements
            replacements.extend(pronoun_replacements)

            # Step 6.5: Catch unclustered generic phrases (e.g. sentence-initial
            # "The Company will…") that fastcoref placed in singleton clusters
            # and that no other pass replaced. Only fires when we have a known
            # global main entity to map them to.
            generic_replacements = find_unclustered_generic_phrases(
                text,
                global_main_entity,
                existing_replacements=replacements,
            )
            if generic_replacements:
                logger.debug(f"Found {len(generic_replacements)} unclustered generic phrases:")
                for s, e, repl in generic_replacements:
                    logger.debug(f"  ({s}-{e}): '{text[s:e]}' -> '{repl}'")
            replacements.extend(generic_replacements)

            # Step 7: Re-apply overlap removal after adding all new replacements
            replacements = remove_overlapping_replacements(replacements)

            # DEBUG: Log post-normalization count
            logger.debug(f"Replacements after entity normalization: {len(replacements)}")

            # ================================================================

            # Sort by start position (DESCENDING) to replace from end to start
            # This prevents character offset shifts during replacement
            replacements.sort(key=lambda x: x[0], reverse=True)

            # Apply replacements in single pass
            result = text
            for start, end, replacement in replacements:
                result = result[:start] + replacement + result[end:]

            # One INFO summary line per resolve_text call — enough signal for
            # the hot path without flooding logs.
            logger.info(
                f"fastcoref: clusters={len(clusters)} replacements_applied={len(replacements)}"
            )
            return result

        except Exception as e:
            logger.error(f"Error in fastcoref resolution: {e}")
            # Return original text on error
            return text


def initialize_model(mode="simple"):
    """
    Load the spaCy model and initialize resolver.

    Args:
        mode: "simple" for pronoun-only, "full" for complete coreference
    """
    global nlp, resolver, resolver_mode

    try:
        logger.info("Loading spaCy model: en_core_web_sm...")
        nlp = spacy.load("en_core_web_sm")
        logger.info("Model loaded successfully!")

        # Initialize resolver based on mode
        if mode == "full":
            if not FASTCOREF_AVAILABLE:
                logger.warning("fastcoref not available, falling back to simple mode")
                logger.warning("Install with: pip3 install --break-system-packages fastcoref")
                resolver = PronounResolver(nlp)
                resolver_mode = "simple"
            else:
                try:
                    resolver = FastCorefResolver()
                    resolver_mode = "full"
                    logger.info("Using full coreference resolution with fastcoref")
                except Exception as e:
                    logger.error(f"Failed to load fastcoref: {e}")
                    logger.info("Falling back to simple pronoun resolution")
                    resolver = PronounResolver(nlp)
                    resolver_mode = "simple"
        else:
            resolver = PronounResolver(nlp)
            resolver_mode = "simple"
            logger.info("Using simple pronoun resolution with spaCy NER")

        return True
    except OSError:
        logger.error("spaCy model not found!")
        logger.error("Download it with: python3 -m spacy download en_core_web_sm")
        return False
    except Exception as e:
        logger.error(f"Failed to load model: {e}")
        return False


@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint."""
    return jsonify({"status": "healthy"}), 200


@app.route('/resolve', methods=['POST'])
def resolve_coreferences():
    """
    Resolve pronouns and coreferences in the provided text.
    Uses the current resolver (simple or full) set via /config.

    Expected JSON body: {"text": "text to process"}
    Returns: {"resolved_text": "processed text", "status": "success", "mode": "simple|full"}
    """
    try:
        data = request.get_json()

        if not data or 'text' not in data:
            return jsonify({"error": "Missing 'text' field in request"}), 400

        text = data['text']

        if not isinstance(text, str):
            return jsonify({"error": "'text' must be a string"}), 400

        if not text.strip():
            return jsonify({"resolved_text": text, "status": "success", "mode": resolver_mode}), 200

        # Use global resolver (can be PronounResolver or FastCorefResolver)
        resolved_text = resolver.resolve_text(text)

        return jsonify({
            "resolved_text": resolved_text,
            "mode": resolver_mode,
            "status": "success"
        }), 200

    except Exception as e:
        logger.error(f"Error processing request: {e}")
        return jsonify({"error": str(e), "status": "error"}), 500


@app.route('/config', methods=['POST'])
def configure_resolver():
    """
    Configure the resolution mode.

    Request body: {"mode": "simple" | "full"}
    Response: {"mode": "simple|full", "fastcoref_available": bool, "status": "success"}
    """
    try:
        data = request.get_json()

        if not data or 'mode' not in data:
            return jsonify({"error": "Missing 'mode' field in request"}), 400

        mode = data['mode']

        if mode not in ("simple", "full"):
            return jsonify({"error": "mode must be 'simple' or 'full'"}), 400

        # Reinitialize with new mode
        success = initialize_model(mode=mode)

        if not success:
            return jsonify({"error": "Failed to initialize resolver", "status": "error"}), 500

        return jsonify({
            "mode": resolver_mode,
            "fastcoref_available": FASTCOREF_AVAILABLE,
            "status": "success"
        }), 200

    except Exception as e:
        logger.error(f"Error configuring resolver: {e}")
        return jsonify({"error": str(e), "status": "error"}), 500


@app.route('/', methods=['GET'])
def index():
    """Root endpoint with service information."""
    return jsonify({
        "service": "pronounCer Service",
        "version": "2.0",
        "description": "Pronoun and coreference resolution service",
        "current_mode": resolver_mode,
        "fastcoref_available": FASTCOREF_AVAILABLE,
        "endpoints": {
            "GET /health": "Health check",
            "POST /resolve": "Resolve pronouns/coreferences (body: {'text': 'text to process'})",
            "POST /config": "Configure resolution mode (body: {'mode': 'simple|full'})",
            "GET /": "This information"
        }
    }), 200


def main():
    """Start the service."""
    logger.info("Starting pronounCer Service...")

    # Initialize the model
    if not initialize_model():
        logger.error("Failed to initialize NLP model. Exiting.")
        sys.exit(1)

    # Start Flask server
    logger.info("Starting Flask server on http://localhost:5050")
    logger.info("Press Ctrl+C to stop the service")

    try:
        app.run(host='127.0.0.1', port=5050, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        logger.info("\nShutting down service...")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Service error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
