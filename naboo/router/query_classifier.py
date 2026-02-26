"""Query complexity classification for intelligent model routing."""

import re
import time
from enum import Enum
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
import threading


class QueryComplexity(Enum):
    """Query complexity levels for model routing."""
    
    SIMPLE = "simple"           # Greetings, simple facts, basic commands
    MODERATE = "moderate"       # Multi-step reasoning, context-dependent
    COMPLEX = "complex"         # Deep reasoning, multi-turn, creative tasks
    CURRENT_INFO = "current_info"  # Queries needing real-time/current information


@dataclass
class CachedClassification:
    """Cached classification result with TTL."""
    
    complexity: QueryComplexity
    timestamp: float
    ttl: float
    
    def is_expired(self) -> bool:
        """Check if cache entry has expired."""
        return time.time() > (self.timestamp + self.ttl)


class QueryClassifier:
    """
    Classify query complexity using heuristics.
    
    Classification is based on:
    - Query length
    - Keyword patterns
    - Sentence structure
    - Question types
    """
    
    # Patterns for simple queries
    GREETING_PATTERNS = [
        r'\b(hello|hi|hey|good morning|good afternoon|good evening)\b',
        r'\b(how are you|what\'s up|sup)\b',
        r'\b(bye|goodbye|see you|later)\b',
        r'\b(thanks|thank you|thx)\b',
        r'\b(yes|no|ok|okay|sure|nope)\b',
    ]

    # Simple factual questions — handled by 3b (fast)
    SIMPLE_FACT_PATTERNS = [
        r'\bwhat is \d',                               # "what is 2 plus 2"
        r'\bwhat\'?s \d',                              # "what's 5 times 3"
        r'\b(\d+)\s*(plus|minus|times|divided by|multiplied by|\+|\-|\*|\/)\s*(\d+)\b',
        r'\bwhat (colour|color) is\b',                 # "what colour is Ziggy's..."
        r'\bwhat is (his|her|their|my|your) (favourite|favorite)\b',
        r'\bwhat (is|are) (Arsenal|Chelsea|Liverpool|Tottenham|the)\b',  # football facts
        r'\bwho (is|are) (Ziggy|Lev|naboo)\b',         # family/identity questions
        r'\bhow old is\b',
        r'\bwhat does .{1,20} mean\b',                  # short definition questions
    ]
    
    # Patterns for simple commands
    SIMPLE_COMMAND_PATTERNS = [
        r'\b(move|turn|stop|go)\b',
        r'\b(forward|backward|left|right)\b',
        r'\b(play|speak|say)\b',
    ]
    
    # Patterns for moderate complexity
    MODERATE_PATTERNS = [
        r'\b(how|why|when|where|who)\b',
        r'\b(explain|describe|tell me about)\b',
        r'\b(what is|what are|what does)\b',
    ]
    
    # Patterns for complex queries
    COMPLEX_PATTERNS = [
        r'\b(analyze|compare|evaluate|assess)\b',
        r'\b(create|generate|write|compose)\b',
        r'\b(imagine|suppose|what if)\b',
        r'\b(multiple|several|various)\b',
    ]
    
    # Patterns for current information queries (real-time/up-to-date info)
    CURRENT_INFO_PATTERNS = [
        # Temporal keywords
        r'\b(latest|recent|current|this week|this month|this year)\b',
        # News and updates
        r'\b(news|update|announcement|release|launched|announced)\b',
        # Prices and availability
        r'\b(price|cost|availability|stock|in stock|out of stock)\b',
        # Current events
        r'\b(what\'s happening|what is happening|what happened)\b',
    ]

    # Tool-backed queries — route to SIMPLE (3b) since the tool does the work
    TOOL_BACKED_PATTERNS = [
        r'\b(weather|forecast|temperature|rain|sunny|cloudy|windy)\b',
        r'\b(score|result|match|game|won|lost|playing)\b',
    ]
    
    def __init__(
        self,
        cache_ttl: float = 300.0,
        custom_current_info_patterns: Optional[List[str]] = None
    ):
        """
        Initialize query classifier.
        
        Args:
            cache_ttl: Time-to-live for cached classifications (seconds)
            custom_current_info_patterns: Optional custom patterns for current info detection
        """
        self.cache_ttl = cache_ttl
        self._cache: Dict[str, CachedClassification] = {}
        self._lock = threading.Lock()
        
        # Compile regex patterns for efficiency
        self._greeting_regex = [re.compile(p, re.IGNORECASE) for p in self.GREETING_PATTERNS]
        self._simple_fact_regex = [re.compile(p, re.IGNORECASE) for p in self.SIMPLE_FACT_PATTERNS]
        self._simple_command_regex = [re.compile(p, re.IGNORECASE) for p in self.SIMPLE_COMMAND_PATTERNS]
        self._tool_backed_regex = [re.compile(p, re.IGNORECASE) for p in self.TOOL_BACKED_PATTERNS]
        self._moderate_regex = [re.compile(p, re.IGNORECASE) for p in self.MODERATE_PATTERNS]
        self._complex_regex = [re.compile(p, re.IGNORECASE) for p in self.COMPLEX_PATTERNS]
        
        # Compile current info patterns (use custom if provided)
        current_info_patterns = custom_current_info_patterns or self.CURRENT_INFO_PATTERNS
        self._current_info_regex = [re.compile(p, re.IGNORECASE) for p in current_info_patterns]
    
    def classify_query(
        self,
        query: str,
        context: Optional[str] = None
    ) -> QueryComplexity:
        """
        Classify query complexity using heuristics.
        
        Args:
            query: User query to classify
            context: Optional conversation context
            
        Returns:
            QueryComplexity enum value
        """
        # Check cache first
        cached = self.get_cached_classification(query)
        if cached is not None:
            return cached
        
        # Normalize query
        query_lower = query.lower().strip()
        query_length = len(query)
        
        # Count sentences (rough heuristic)
        sentence_count = len([s for s in query.split('.') if s.strip()])
        
        # Tool-backed queries: route to SIMPLE (3b) — the tool fetches the data,
        # the model just needs to format a sentence. Must check before CURRENT_INFO.
        for pattern in self._tool_backed_regex:
            if pattern.search(query_lower):
                complexity = QueryComplexity.SIMPLE
                self.cache_classification(query, complexity)
                return complexity

        # Check for current info patterns FIRST (highest priority for web grounding)
        if self.needs_current_info(query):
            complexity = QueryComplexity.CURRENT_INFO
            self.cache_classification(query, complexity)
            return complexity
        
        # Check for complex patterns (second highest priority)
        for pattern in self._complex_regex:
            if pattern.search(query_lower):
                complexity = QueryComplexity.COMPLEX
                self.cache_classification(query, complexity)
                return complexity
        
        # Long queries with multiple sentences are complex
        if query_length > 200 or sentence_count > 3:
            complexity = QueryComplexity.COMPLEX
            self.cache_classification(query, complexity)
            return complexity

        # Check for simple facts (short factual questions handled by 3b)
        # Must come BEFORE moderate check — "what is X" matches both
        if query_length < 80:
            for pattern in self._simple_fact_regex:
                if pattern.search(query_lower):
                    complexity = QueryComplexity.SIMPLE
                    self.cache_classification(query, complexity)
                    return complexity

        # Check for moderate patterns (before simple checks)
        for pattern in self._moderate_regex:
            if pattern.search(query_lower):
                complexity = QueryComplexity.MODERATE
                self.cache_classification(query, complexity)
                return complexity
        
        # Check for greeting patterns (SIMPLE) - but only for very short queries
        if query_length < 20:
            for pattern in self._greeting_regex:
                if pattern.search(query_lower):
                    complexity = QueryComplexity.SIMPLE
                    self.cache_classification(query, complexity)
                    return complexity
        
        # Check for simple commands (SIMPLE) - only for short queries
        if query_length < 40:
            for pattern in self._simple_command_regex:
                if pattern.search(query_lower):
                    complexity = QueryComplexity.SIMPLE
                    self.cache_classification(query, complexity)
                    return complexity
        
        # Very short queries are usually simple
        if query_length < 20:
            complexity = QueryComplexity.SIMPLE
            self.cache_classification(query, complexity)
            return complexity
        
        # Medium-length queries default to moderate
        if 20 <= query_length <= 200:
            complexity = QueryComplexity.MODERATE
            self.cache_classification(query, complexity)
            return complexity
        
        # Default to simple for anything else (shouldn't reach here often)
        complexity = QueryComplexity.SIMPLE
        self.cache_classification(query, complexity)
        return complexity
    
    def needs_current_info(self, query: str) -> bool:
        """
        Check if query requires current/real-time information.
        
        This method detects queries about:
        - Latest/recent/current events
        - News, updates, announcements
        - Prices, costs, availability
        - Weather, scores, results
        
        Args:
            query: User query to check
            
        Returns:
            True if query needs current information, False otherwise
        """
        query_lower = query.lower().strip()
        for pattern in self._current_info_regex:
            if pattern.search(query_lower):
                return True
        return False
    
    def get_cached_classification(self, query: str) -> Optional[QueryComplexity]:
        """
        Check cache for previous classification.
        
        Args:
            query: Query to look up
            
        Returns:
            Cached complexity or None if not found/expired
        """
        # Use query as cache key (could hash for very long queries)
        cache_key = query.strip().lower()
        
        with self._lock:
            cached = self._cache.get(cache_key)
            
            if cached is None:
                return None
            
            # Check if expired
            if cached.is_expired():
                del self._cache[cache_key]
                return None
            
            return cached.complexity
    
    def cache_classification(
        self,
        query: str,
        complexity: QueryComplexity,
        ttl: Optional[float] = None
    ) -> None:
        """
        Cache classification result.
        
        Args:
            query: Query that was classified
            complexity: Classification result
            ttl: Optional custom TTL (uses default if None)
        """
        cache_key = query.strip().lower()
        
        with self._lock:
            self._cache[cache_key] = CachedClassification(
                complexity=complexity,
                timestamp=time.time(),
                ttl=ttl if ttl is not None else self.cache_ttl
            )
    
    def clear_cache(self) -> None:
        """Clear all cached classifications."""
        with self._lock:
            self._cache.clear()
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.
        
        Returns:
            Dictionary with cache stats
        """
        with self._lock:
            total_entries = len(self._cache)
            
            # Count expired entries
            expired_count = sum(
                1 for cached in self._cache.values()
                if cached.is_expired()
            )
            
            # Count by complexity
            complexity_counts = {
                QueryComplexity.SIMPLE: 0,
                QueryComplexity.MODERATE: 0,
                QueryComplexity.COMPLEX: 0,
                QueryComplexity.CURRENT_INFO: 0,
            }
            
            for cached in self._cache.values():
                if not cached.is_expired():
                    complexity_counts[cached.complexity] += 1
            
            return {
                "total_entries": total_entries,
                "active_entries": total_entries - expired_count,
                "expired_entries": expired_count,
                "by_complexity": {
                    "simple": complexity_counts[QueryComplexity.SIMPLE],
                    "moderate": complexity_counts[QueryComplexity.MODERATE],
                    "complex": complexity_counts[QueryComplexity.COMPLEX],
                    "current_info": complexity_counts[QueryComplexity.CURRENT_INFO],
                },
            }
    
    def cleanup_expired(self) -> int:
        """
        Remove expired entries from cache.
        
        Returns:
            Number of entries removed
        """
        with self._lock:
            expired_keys = [
                key for key, cached in self._cache.items()
                if cached.is_expired()
            ]
            
            for key in expired_keys:
                del self._cache[key]
            
            return len(expired_keys)
