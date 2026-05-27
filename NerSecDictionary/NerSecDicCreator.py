#!/usr/bin/env python3
"""
NerSecDicCreator.py - Fast Financial Named Entity Recognition using SEC EDGAR + Custom Aliases

Fast NER script using:
- Pre-cached SEC EDGAR data (7-day TTL)
- Custom alias mappings (Inc., Corp., Ltd. variations)
- Optional yfinance enrichment (24-hour TTL)
- Parallel sentence processing
"""

import json
import os
import re
import sys
import argparse
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
import pickle
import urllib.request
import time

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Distinguish "not in cache" from "cached as None" without an extra membership check.
_SENTINEL = object()


class CacheManager:
    """Manages cache lifecycle (validation, loading, saving, expiration)"""

    CACHE_DIR = Path.home() / '.cache' / 'NerSecDictionary'
    SEC_CACHE_FILE = CACHE_DIR / 'sec_tickers.json'
    ALIASES_CACHE_FILE = CACHE_DIR / 'sec_aliases.json'
    METADATA_CACHE_FILE = CACHE_DIR / 'cache_metadata.json'
    RESOLUTION_CACHE_FILE = CACHE_DIR / 'entity_resolutions.pkl'
    YFINANCE_CACHE_DIR = CACHE_DIR / 'yfinance'

    SEC_CACHE_TTL_DAYS = 7
    YFINANCE_CACHE_TTL_HOURS = 24

    @classmethod
    def ensure_cache_dir(cls):
        """Create cache directory if it doesn't exist"""
        cls.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cls.YFINANCE_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    @classmethod
    def is_cache_valid(cls) -> bool:
        """Check if SEC cache exists and is not expired"""
        if not cls.SEC_CACHE_FILE.exists() or not cls.ALIASES_CACHE_FILE.exists():
            return False

        if not cls.METADATA_CACHE_FILE.exists():
            return False

        try:
            with open(cls.METADATA_CACHE_FILE) as f:
                metadata = json.load(f)

            expires_at_str = metadata['sec_data']['expires_at']
            # Remove 'Z' suffix and parse as naive datetime
            if expires_at_str.endswith('Z'):
                expires_at_str = expires_at_str[:-1]
            expires_at = datetime.fromisoformat(expires_at_str)
            return datetime.now() < expires_at
        except (json.JSONDecodeError, KeyError, ValueError):
            return False

    @classmethod
    def load_cache(cls) -> Tuple[Dict, Dict]:
        """Load SEC tickers and aliases from cache"""
        cls.ensure_cache_dir()

        if not cls.is_cache_valid():
            return None, None

        try:
            with open(cls.SEC_CACHE_FILE) as f:
                sec_tickers = json.load(f)
            with open(cls.ALIASES_CACHE_FILE) as f:
                aliases = json.load(f)
            logger.info(f"Loaded cache: {len(sec_tickers)} companies")
            return sec_tickers, aliases
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Cache load failed: {e}")
            return None, None

    @classmethod
    def save_cache(cls, sec_tickers: Dict, aliases: Dict):
        """Save SEC tickers and aliases to cache"""
        cls.ensure_cache_dir()

        try:
            with open(cls.SEC_CACHE_FILE, 'w') as f:
                json.dump(sec_tickers, f)
            with open(cls.ALIASES_CACHE_FILE, 'w') as f:
                json.dump(aliases, f)

            metadata = {
                'sec_data': {
                    'last_updated': datetime.now().isoformat(),
                    'expires_at': (datetime.now() + timedelta(days=cls.SEC_CACHE_TTL_DAYS)).isoformat(),
                    'source_url': 'https://www.sec.gov/files/company_tickers.json',
                    'total_companies': len(sec_tickers),
                    'version': '1.0'
                },
                'yfinance_cache': {
                    'ttl_hours': cls.YFINANCE_CACHE_TTL_HOURS,
                    'total_cached_tickers': 0
                }
            }
            with open(cls.METADATA_CACHE_FILE, 'w') as f:
                json.dump(metadata, f, indent=2)

            logger.info(f"Cached {len(sec_tickers)} companies")
        except IOError as e:
            logger.error(f"Cache save failed: {e}")

    @classmethod
    def load_resolution_cache(cls) -> Optional[Dict]:
        """Load the persisted entity_text -> resolution dict cache.

        Returns None if the file is missing or older than SEC_CACHE_TTL_DAYS;
        a stale resolution may point at a CIK/ticker that has since changed,
        so we expire it on the same cadence as the SEC ticker file itself.
        """
        f = cls.RESOLUTION_CACHE_FILE
        if not f.exists():
            return None
        age_days = (datetime.now().timestamp() - f.stat().st_mtime) / 86400.0
        if age_days >= cls.SEC_CACHE_TTL_DAYS:
            logger.info(f"Resolution cache expired ({age_days:.1f}d old); ignoring")
            return None
        try:
            with open(f, 'rb') as fp:
                return pickle.load(fp)
        except Exception as e:
            logger.warning(f"Resolution cache load failed: {e}")
            return None

    @classmethod
    def save_resolution_cache(cls, cache: Dict) -> None:
        """Pickle the entity-resolution cache to disk."""
        cls.ensure_cache_dir()
        try:
            tmp = cls.RESOLUTION_CACHE_FILE.with_suffix('.pkl.tmp')
            with open(tmp, 'wb') as fp:
                pickle.dump(cache, fp, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(tmp, cls.RESOLUTION_CACHE_FILE)
            logger.info(f"Saved {len(cache)} entity resolutions to {cls.RESOLUTION_CACHE_FILE}")
        except Exception as e:
            logger.error(f"Resolution cache save failed: {e}")


class TickerResolver:
    """Resolves company names/aliases to ticker symbols with multi-tier matching"""

    SEC_EDGAR_URL = 'https://www.sec.gov/files/company_tickers.json'

    # Aliases that match too many things; kept in __init__ scope so the
    # fuzzy-match fast-path uses the exact same blacklist as the legacy code.
    _GENERIC_ALIASES = frozenset({
        'medical inc', 'medical corp', 'technology inc', 'technology corp',
        'inc', 'corp', 'ltd', 'company', 'holdings', 'group',
    })

    def __init__(self, force_rebuild: bool = False):
        """Initialize resolver with cache-first approach"""
        self.sec_tickers = {}
        self.aliases = {}
        self.ticker_to_info = {}
        # Pre-filtered alias map for fuzzy match: alias -> (ticker, frozenset(words), specificity)
        self._fuzzy_aliases: Dict[str, Tuple[str, frozenset, int]] = {}
        # Inverted index: significant word (> 3 chars) -> list of alias keys
        self._word_to_aliases: Dict[str, List[str]] = {}
        # Persistent entity-text -> resolution dict cache (loaded from disk if present)
        self._resolution_cache: Dict[str, Optional[Dict]] = {}
        self._resolution_cache_max = 50_000

        if force_rebuild:
            logger.info("Force rebuilding cache...")
            success = self._build_cache()
            if not success:
                logger.error("Cache build failed, using fallback")
                self.sec_tickers = self._get_fallback_sec_data()
                self._build_ticker_to_info()
                self._build_alias_map()
        else:
            if not self._load_from_cache():
                logger.info("Cache missing or expired, building...")
                success = self._build_cache()
                if not success:
                    logger.error("Cache build failed, using fallback")
                    self.sec_tickers = self._get_fallback_sec_data()
                    self._build_ticker_to_info()
                    self._build_alias_map()

        # Build inverted index regardless of which load path ran above.
        # `_build_alias_map` doesn't always fire (cache-hit path skips it),
        # so the index is built here once we know `self.aliases` is populated.
        self._build_fuzzy_index()

        # Load any previously-saved entity resolutions; safe to skip on miss.
        loaded = CacheManager.load_resolution_cache()
        if loaded:
            self._resolution_cache = loaded
            logger.info(f"Loaded {len(loaded)} cached entity resolutions from disk")

    def _load_from_cache(self) -> bool:
        """Try loading from cache"""
        self.sec_tickers, self.aliases = CacheManager.load_cache()
        if self.sec_tickers and self.aliases:
            self._build_ticker_to_info()
            return True
        return False

    def _build_cache(self) -> bool:
        """Download and build cache"""
        self.sec_tickers = self._download_sec_data()
        if not self.sec_tickers:
            logger.error("Failed to download SEC data")
            return False

        self._build_ticker_to_info()
        self._build_alias_map()
        CacheManager.save_cache(self.sec_tickers, self.aliases)
        return True

    def _download_sec_data(self) -> Dict:
        """Download SEC EDGAR company data"""
        try:
            logger.info("Downloading SEC EDGAR data...")
            req = urllib.request.Request(
                self.SEC_EDGAR_URL,
                headers={'User-Agent': 'NerSecDictionary/1.0 (nersec@example.com)'}
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))
            # SEC returns object with numeric keys, convert to flat list
            return {str(v['cik_str']): v for v in data.values()}
        except Exception as e:
            logger.warning(f"SEC download failed ({e}), using fallback database")
            return self._get_fallback_sec_data()

    def _get_fallback_sec_data(self) -> Dict:
        """Fallback database with common tech/finance companies"""
        fallback_data = {
            '0001018724': {'ticker': 'AMZN', 'title': 'Amazon.com Inc.', 'cik_str': 1018724},
            '0000789019': {'ticker': 'AAPL', 'title': 'Apple Inc.', 'cik_str': 789019},
            '0001652044': {'ticker': 'GOOGL', 'title': 'Alphabet Inc.', 'cik_str': 1652044},
            '0000051143': {'ticker': 'MSFT', 'title': 'Microsoft Corporation', 'cik_str': 51143},
            '0000320193': {'ticker': 'AAPL', 'title': 'Apple Inc.', 'cik_str': 320193},
            '0001326706': {'ticker': 'FEED', 'title': 'ENvue Medical, Inc.', 'cik_str': 1326706},
            '0001010638': {'ticker': 'TSLA', 'title': 'Tesla Inc.', 'cik_str': 1010638},
            '0000789019': {'ticker': 'JPM', 'title': 'JPMorgan Chase & Co.', 'cik_str': 789019},
            '0000100493': {'ticker': 'KO', 'title': 'The Coca-Cola Company', 'cik_str': 100493},
            '0000051587': {'ticker': 'WMT', 'title': 'Walmart Inc.', 'cik_str': 51587},
        }
        logger.info(f"Using fallback database with {len(fallback_data)} companies")
        return fallback_data

    @staticmethod
    def _normalize_company_name(text: str) -> str:
        """Normalize company name for better matching.

        Removes punctuation, normalizes whitespace, and converts to lowercase.
        Example: "ENvue Medical, Inc." -> "envue medical inc"
        """
        # Remove commas and periods
        normalized = text.replace(',', '').replace('.', ' ')
        # Normalize whitespace
        normalized = ' '.join(normalized.split())
        return normalized.lower().strip()

    def _build_ticker_to_info(self):
        """Build ticker -> company info mapping"""
        self.ticker_to_info = {}
        for cik, company in self.sec_tickers.items():
            ticker = company.get('ticker', '').upper()
            if ticker:
                self.ticker_to_info[ticker] = {
                    'ticker': ticker,
                    'official_name': company.get('title', ''),
                    'cik': cik
                }

    def _build_alias_map(self):
        """Build comprehensive alias map (company name -> ticker)"""
        self.aliases = {}

        for cik, company in self.sec_tickers.items():
            ticker = company.get('ticker', '').upper()
            title = company.get('title', '')

            if not ticker or not title:
                continue

            # Add exact company name
            self.aliases[title.lower()] = ticker

            # Add normalized version (e.g., "ENvue Medical, Inc." -> "envue medical inc")
            normalized_title = self._normalize_company_name(title)
            self.aliases[normalized_title] = ticker

            # Add variations without suffix
            for suffix in [
                # US suffixes
                ' Inc.', ' Inc', ' Corp.', ' Corp', ' Ltd.', ' Ltd',
                ' LLC', ' L.L.C.', ' Co.', ' Company',
                # International suffixes (common European/Latin American forms)
                ' SA', ' S.A.', ' NV', ' N.V.', ' GmbH', ' AG', ' AB',
                ' SpA', ' S.p.A.', ' Oy', ' PLC', ' Plc',
                ' Pte Ltd', ' Pte. Ltd.', ' Pty Ltd', ' Pty. Ltd.',
                # Asian forms
                ' KK', ' KG', ' Bhd', ' Berhad'
            ]:
                if title.endswith(suffix):
                    base_name = title[:-len(suffix)]
                    self.aliases[base_name.lower()] = ticker
                    # Also add normalized version of base name
                    normalized_base = self._normalize_company_name(base_name)
                    self.aliases[normalized_base] = ticker

            # Add common abbreviations
            parts = title.split()
            if len(parts) > 1:
                # First + last word
                abbreviated = f"{parts[0]} {parts[-1]}"
                self.aliases[abbreviated.lower()] = ticker
                # Also add normalized version of abbreviation
                normalized_abbr = self._normalize_company_name(abbreviated)
                self.aliases[normalized_abbr] = ticker

    def resolve_entity_to_ticker(self, entity_text: str) -> Optional[Dict]:
        """Resolve entity text to ticker with multi-tier matching.

        Uses an instance-level dict cache (persisted to disk via save_cache()
        for warm cold-starts) so the expensive Tier 3 fuzzy match runs at most
        once per unique entity text.
        """
        if not entity_text:
            return None

        cached = self._resolution_cache.get(entity_text, _SENTINEL)
        if cached is not _SENTINEL:
            return cached

        result = self._resolve_uncached(entity_text)

        # FIFO eviction once we hit the cap (dict insertion order in 3.7+).
        if len(self._resolution_cache) >= self._resolution_cache_max:
            oldest = next(iter(self._resolution_cache))
            del self._resolution_cache[oldest]
        self._resolution_cache[entity_text] = result
        return result

    def _resolve_uncached(self, entity_text: str) -> Optional[Dict]:
        # Tier 1: Exact ticker match
        ticker_match = self._match_ticker_exact(entity_text)
        if ticker_match:
            return {
                'ticker': ticker_match,
                'official_name': self.ticker_to_info[ticker_match]['official_name'],
                'cik': self.ticker_to_info[ticker_match]['cik'],
                'match_type': 'ticker'
            }

        # Tier 2: Alias match
        alias_match = self._match_alias(entity_text)
        if alias_match:
            return {
                'ticker': alias_match,
                'official_name': self.ticker_to_info[alias_match]['official_name'],
                'cik': self.ticker_to_info[alias_match]['cik'],
                'match_type': 'company_name'
            }

        # Tier 3: Fuzzy name match (partial match)
        fuzzy_match = self._match_fuzzy_name(entity_text)
        if fuzzy_match:
            return {
                'ticker': fuzzy_match,
                'official_name': self.ticker_to_info[fuzzy_match]['official_name'],
                'cik': self.ticker_to_info[fuzzy_match]['cik'],
                'match_type': 'alias'
            }

        return None

    def save_cache(self) -> None:
        """Persist the in-memory entity-resolution cache to disk.

        Intended to be called by the orchestrator on shutdown (e.g. SIGTERM
        handler in the WebSocket service). Calling on every process() would
        re-introduce hot-path I/O — don't.
        """
        CacheManager.save_resolution_cache(self._resolution_cache)

    def _build_fuzzy_index(self) -> None:
        """Pre-filter aliases that could ever pass fuzzy match and build an
        inverted index mapping each significant alias word to its alias keys.

        Cuts per-uncached-entity fuzzy-match cost from O(N=26K aliases) to
        O(few candidates) without changing match semantics.
        """
        self._fuzzy_aliases = {}
        self._word_to_aliases = {}
        for alias, ticker in self.aliases.items():
            if alias in self._GENERIC_ALIASES:
                continue
            alias_words = frozenset(alias.split())
            significant = {w for w in alias_words if len(w) > 3}
            # Existing rule: ≥ 2 significant words required for fuzzy match.
            if len(significant) < 2:
                continue
            self._fuzzy_aliases[alias] = (ticker, alias_words, len(alias_words))
            for w in significant:
                self._word_to_aliases.setdefault(w, []).append(alias)

    def _match_ticker_exact(self, text: str) -> Optional[str]:
        """Check if text is an exact ticker symbol"""
        ticker = text.strip().upper()
        if 1 <= len(ticker) <= 5 and ticker.isalpha():
            if ticker in self.ticker_to_info:
                return ticker
        return None

    # Long-form legal suffixes absent from the alias-build suffix list (which
    # only strips abbreviated forms like "Ltd", "Inc", "Corp").  Stripping
    # these on the lookup side lets "Dreamland Limited" resolve to the same
    # ticker as SEC-registered "Dreamland Ltd".
    _LONG_FORM_SUFFIXES = (' limited', ' incorporated', ' corporation')

    def _match_alias(self, text: str) -> Optional[str]:
        """Check if text matches a company alias.

        First tries exact match, then tries normalized match (punctuation-stripped).
        Falls back to stripping long-form legal suffixes so that e.g.
        "Dreamland Limited" resolves to the same ticker as "Dreamland Ltd".
        Example: "ENvue Medical Inc" matches normalized "ENvue Medical, Inc."
        """
        text_lower = text.lower().strip()
        # Try exact match first
        if text_lower in self.aliases:
            return self.aliases[text_lower]
        # Try normalized match (removes punctuation)
        text_normalized = self._normalize_company_name(text)
        result = self.aliases.get(text_normalized)
        if result:
            return result
        # Try stripping long-form suffixes not covered by the alias build step
        for suffix in self._LONG_FORM_SUFFIXES:
            if text_normalized.endswith(suffix):
                base = text_normalized[:-len(suffix)].strip()
                result = self.aliases.get(base)
                if result:
                    return result
        return None

    def _match_fuzzy_name(self, text: str) -> Optional[str]:
        """Fuzzy match by checking if text contains company name parts.

        Same semantics as the legacy linear scan (rule preserved):
        1. At least 2 significant words (length > 3) in the alias
        2. All alias words present in the entity text
        3. Not matching pure generic patterns

        Implementation uses the inverted index built in _build_fuzzy_index:
        only aliases that share at least one significant word with the entity
        are considered. Any alias that could possibly satisfy the rule above
        must share ≥ 1 significant word with the entity, so the candidate set
        is a complete superset of the legacy result — same matches, far less
        scanning.
        """
        words = set(text.lower().strip().split())

        # If the entity has no significant words, no alias can satisfy the rule.
        candidates = set()
        for w in words:
            if len(w) > 3:
                postings = self._word_to_aliases.get(w)
                if postings:
                    candidates.update(postings)

        if not candidates:
            return None

        best_match = None
        best_specificity = 0
        for alias in candidates:
            ticker, alias_words, specificity = self._fuzzy_aliases[alias]
            if not alias_words.issubset(words):
                continue
            if specificity > best_specificity:
                best_match = ticker
                best_specificity = specificity

        return best_match


class EntityExtractor:
    """Extract potential entities (tickers and company names) from text"""

    # Regex patterns
    TICKER_PATTERN = re.compile(r'\b[A-Z]{1,5}\b')
    # Matches tickers only in parenthetical financial notation.
    # Two patterns are needed:
    # 1. Bare or exchange-prefixed with immediate close: (TDIC) or (NYSE: TDIC)
    # 2. Exchange-prefixed with trailing content: (Nasdaq: BWEN, or the "Company")
    #    — the comma after the ticker breaks pattern 1, but the exchange prefix is
    #    an unambiguous financial marker so loosening the close is safe here.
    _BARE_TICKER_PATTERN = re.compile(r'\((?:[A-Za-z]{1,8}\s*:\s*)?([A-Z]{1,5})\)')
    _EXCHANGE_TICKER_PATTERN = re.compile(r'\([A-Za-z]{1,8}\s*:\s*([A-Z]{1,5})')
    COMPANY_SUFFIXES = {
        'Inc.', 'Inc', 'Corp.', 'Corp', 'Ltd.', 'Ltd',
        'LLC', 'L.L.C.', 'Co.', 'Company', 'Incorporated',
        'Corporation', 'Limited', 'Holdings', 'Group'
    }

    # Pre-compile the per-suffix company-name patterns once (was re-compiling
    # all 16 of them on every sentence). Order doesn't matter for correctness
    # because matches are deduped via `seen` in extract_entities.
    _COMPANY_SUFFIX_PATTERNS = [
        (
            suffix,
            re.compile(r'\b([A-Z][a-zA-Z]*(?:\s+[A-Z][a-zA-Z]*)*)\s+' + re.escape(suffix) + r'\b'),
        )
        for suffix in COMPANY_SUFFIXES
    ]

    # Two consecutive capitalized words ("ENvue Medical").
    _CAPITALIZED_PHRASE_PATTERN = re.compile(r'\b([A-Z][a-zA-Z]+\s+[A-Z][a-zA-Z]+)\b')

    # Common English words that are capitalized only because they open a sentence
    # or a quoted clause. After coreference resolution these can appear immediately
    # before a company name, producing spurious two-word phrases like "While Broadwind".
    _SENTENCE_INITIAL_WORDS = frozenset({
        'While', 'During', 'After', 'Before', 'When', 'Although', 'However',
        'Therefore', 'Nevertheless', 'Furthermore', 'Meanwhile', 'Because',
        'Since', 'Until', 'Unless', 'Whether', 'Despite', 'Among', 'Between',
        'Within', 'Without', 'According', 'Including', 'Following', 'Through',
        'Upon', 'Under', 'Over', 'Beyond', 'Across', 'Against',
    })

    FALSE_POSITIVES = {
        'CEO', 'CFO', 'CTO', 'FDA', 'SEC', 'USA', 'NYSE', 'NASDAQ',
        'ETF', 'API', 'XML', 'HTML', 'URL', 'URI', 'OTC', 'IPO',
        'Q1', 'Q2', 'Q3', 'Q4', 'AM', 'PM', 'ET', 'PST', 'EST',
        'The', 'We', 'With', 'Photo', 'Price', 'Action', 'Chief',
        'Executive', 'Officer', 'Medical', 'Syringes', 'Deliver',
        'Wednesday', 'Amazon', 'Benzinga',
        # Multi-word false positives
        'Home Care', 'Distribution Deal', 'Price Action', 'Chief Executive',
        'Executive Officer', 'Medical Technology', 'Reusable Syringes',
        'Care Channels', 'Feeding Tube', 'Session Volume',
        'Pushes Reusable', 'OTC Syringes', 'Into Home', 'Care With',
        'U Deliver', 'Doron Besser', 'Benzinga Pro', 'ENFit Syringes',
        'Wholesale Channels'
    }

    @staticmethod
    def extract_entities(text: str) -> List[Tuple[str, int, int]]:
        """
        Extract potential entities from text.
        Returns list of (entity_text, char_start, char_end) tuples.
        """
        entities = []
        seen = {}  # Track seen entities to avoid duplicates

        # Extract ticker symbols only from parenthetical notation.
        # Two patterns are used (see class attributes for rationale):
        #   _BARE_TICKER_PATTERN:     (TDIC) or (NYSE: TDIC)
        #   _EXCHANGE_TICKER_PATTERN: (Nasdaq: BWEN, or the "Company")
        # The exchange pattern is a superset of the bare-with-exchange case, so
        # deduplication via `seen` prevents double-adding the same ticker.
        for pattern in (EntityExtractor._BARE_TICKER_PATTERN,
                        EntityExtractor._EXCHANGE_TICKER_PATTERN):
            for match in pattern.finditer(text):
                ticker = match.group(1)
                if ticker not in EntityExtractor.FALSE_POSITIVES and 2 <= len(ticker) <= 5:
                    if ticker not in seen:
                        entities.append((ticker, match.start(1), match.end(1)))
                        seen[ticker] = (match.start(1), match.end(1))

        # Extract company names with known suffixes (primary strategy)
        # This is most reliable pattern
        for suffix, pattern in EntityExtractor._COMPANY_SUFFIX_PATTERNS:
            for match in pattern.finditer(text):
                company_name = (match.group(1) + ' ' + suffix).strip()
                # Only add if company name is 2+ words
                name_parts = match.group(1).split()
                if len(name_parts) >= 1:  # At least one word before suffix
                    if company_name not in seen:
                        entities.append((company_name, match.start(), match.end()))
                        seen[company_name] = (match.start(), match.end())

        # Extract potential company names (capitalized phrases without suffixes)
        # Pattern: exactly 2 consecutive capitalized words (e.g., "ENvue Medical")
        # Most company names are 2 words; 3+ words are likely false positives
        for match in EntityExtractor._CAPITALIZED_PHRASE_PATTERN.finditer(text):
            phrase = match.group()
            first_word = phrase.split()[0]
            # Skip false positives, already-seen phrases, and phrases whose first
            # word is a common sentence-initial word capitalized by position only
            # (e.g. "While Broadwind" after coreference replacement).
            if (phrase not in seen
                    and phrase not in EntityExtractor.FALSE_POSITIVES
                    and first_word not in EntityExtractor._SENTENCE_INITIAL_WORDS):
                entities.append((phrase, match.start(), match.end()))
                seen[phrase] = (match.start(), match.end())

        return entities


class NERProcessor:
    """Main processor for NER task"""

    def __init__(self, resolver: TickerResolver):
        self.resolver = resolver
        self.extractor = EntityExtractor()

    def process_sentence(self, sentence: Dict) -> Dict:
        """Process single sentence and extract entities"""
        sentence_text = sentence.get('text', '')
        sentence_id = sentence.get('id', -1)
        char_start_offset = sentence.get('char_start', 0)

        # Extract potential entities
        entities = []
        raw_entities = self.extractor.extract_entities(sentence_text)

        for entity_text, start, end in raw_entities:
            # Resolve to ticker
            resolution = self.resolver.resolve_entity_to_ticker(entity_text)

            entity = {
                'text': entity_text,
                'char_start': start,
                'char_end': end,
            }

            if resolution:
                entity.update(resolution)
            else:
                entity['ticker'] = None
                entity['official_name'] = None
                entity['cik'] = None
                entity['match_type'] = 'unresolved'

            entities.append(entity)

        # Return processed sentence
        return {
            'id': sentence_id,
            'text': sentence_text,
            'source': sentence.get('source', ''),
            'entities': entities
        }

    def process_all_sentences(self, sentences: List[Dict], max_workers: int = 4) -> List[Dict]:
        """Process all sentences sequentially.

        Threading was measured to be ~60% slower than sequential here: the
        per-sentence work is regex + dict lookup (GIL-bound) and microscopic
        on warm cache, so thread-pool overhead dominates. The `max_workers`
        argument is kept for backward compatibility with existing callers and
        is intentionally ignored.
        """
        del max_workers  # ignored — see docstring
        processed = []
        for s in sentences:
            try:
                processed.append(self.process_sentence(s))
            except Exception as e:
                logger.error(f"Error processing sentence id={s.get('id')}: {e}")
        return processed


def load_input_file(filepath: str) -> Optional[Dict]:
    """Load and validate input JSON file"""
    try:
        with open(filepath) as f:
            data = json.load(f)

        # Validate structure
        if 'sentences' not in data:
            logger.error("Missing 'sentences' key in input")
            return None

        if not isinstance(data['sentences'], list):
            logger.error("'sentences' must be a list")
            return None

        logger.info(f"Loaded {len(data['sentences'])} sentences")
        return data
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON: {e}")
        return None
    except FileNotFoundError:
        logger.error(f"File not found: {filepath}")
        return None


def save_output_file(output_path: str, output_data: Dict) -> bool:
    """Save output to JSON file"""
    try:
        with open(output_path, 'w') as f:
            json.dump(output_data, f, indent=2)
        logger.info(f"Output saved: {output_path}")
        return True
    except IOError as e:
        logger.error(f"Failed to save output: {e}")
        return False


def get_output_filename(input_path: str) -> str:
    """Generate output filename from input filename"""
    path = Path(input_path)
    stem = path.stem
    suffix = '_NER.json'
    return str(path.parent / (stem + suffix))


def main():
    parser = argparse.ArgumentParser(
        description='Fast Financial NER using SEC EDGAR + Custom Aliases'
    )
    parser.add_argument('--input', '-i', required=False, help='Input JSON file path')
    parser.add_argument('--update-cache', action='store_true',
                       help='Update SEC cache and exit')
    parser.add_argument('--rebuild-cache', action='store_true',
                       help='Force rebuild SEC cache and exit')
    parser.add_argument('--enrich-yfinance', action='store_true',
                       help='Enrich entities with yfinance data (optional, slower)')

    args = parser.parse_args()

    start_time = time.time()

    # Handle cache-only operations
    if args.update_cache or args.rebuild_cache:
        logger.info("Initializing TickerResolver (this may take a moment on first run)...")
        resolver = TickerResolver(force_rebuild=args.rebuild_cache)
        elapsed = time.time() - start_time
        logger.info(f"Cache operation completed in {elapsed:.2f} seconds")
        return

    # Load input file (required for normal operation)
    if not args.input:
        parser.error("--input is required for normal NER processing")

    input_data = load_input_file(args.input)
    if not input_data:
        sys.exit(1)

    sentences = input_data.get('sentences', [])
    metadata = input_data.get('metadata', {})

    # Initialize resolver (builds cache on first run)
    logger.info("Initializing TickerResolver (this may take a moment on first run)...")
    resolver = TickerResolver()

    # Process sentences
    logger.info("Processing sentences...")
    processor = NERProcessor(resolver)
    processed_sentences = processor.process_all_sentences(sentences)

    # Collect statistics
    all_entities = []
    unique_tickers = set()

    for sentence in processed_sentences:
        for entity in sentence.get('entities', []):
            all_entities.append(entity)
            if entity.get('ticker'):
                unique_tickers.add(entity['ticker'])

    # Build output
    elapsed = time.time() - start_time
    output_data = {
        'metadata': {
            'input_file': args.input,
            'total_sentences': len(sentences),
            'total_entities': len(all_entities),
            'unique_tickers': sorted(list(unique_tickers)),
            'processing_time_seconds': round(elapsed, 2)
        },
        'sentences': processed_sentences
    }

    # Save output
    output_path = get_output_filename(args.input)
    if save_output_file(output_path, output_data):
        logger.info(f"Processing complete in {elapsed:.2f} seconds")
        logger.info(f"Found {len(all_entities)} entities, {len(unique_tickers)} unique tickers")
    else:
        sys.exit(1)


if __name__ == '__main__':
    main()
