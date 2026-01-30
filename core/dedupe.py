"""
Inbound Message Deduplication Cache

Prevents processing duplicate incoming messages within a time window.
Based on Clawdbot's inbound-dedupe.ts patterns.

Use cases:
- Webhook retries sending the same message
- User double-tapping send button
- Network issues causing message redelivery
"""
import hashlib
import logging
from collections import OrderedDict
from time import time
from typing import Optional

logger = logging.getLogger("brainmap.dedupe")

# Constants from Clawdbot (inbound-dedupe.ts)
DEDUPE_CACHE_TTL_SECONDS = 1200  # 20 minutes
DEDUPE_CACHE_MAX_SIZE = 5000


class InboundDedupeCache:
    """
    LRU cache with TTL for inbound message deduplication.

    Tracks recently seen message fingerprints to detect and reject
    duplicate incoming messages (from webhooks, retries, etc.).

    Based on Clawdbot's inbound-dedupe.ts.
    """

    def __init__(
        self,
        ttl_seconds: int = DEDUPE_CACHE_TTL_SECONDS,
        max_size: int = DEDUPE_CACHE_MAX_SIZE
    ):
        """
        Initialize dedupe cache.

        Args:
            ttl_seconds: Time-to-live for entries (default 20 minutes)
            max_size: Maximum cache entries (default 5000)
        """
        self._cache: OrderedDict[str, float] = OrderedDict()
        self._ttl = ttl_seconds
        self._max_size = max_size

    def _make_key(
        self,
        user_id: str,
        message: str,
        session_id: Optional[str] = None,
        channel: Optional[str] = None,
        message_id: Optional[str] = None
    ) -> str:
        """
        Create cache key from multiple identifiers.

        From Clawdbot's inbound-dedupe.ts pattern:
        Key includes all available identifiers for comprehensive deduplication.

        Args:
            user_id: User identifier
            message: Message content
            session_id: Session identifier (optional)
            channel: Channel/provider (optional, e.g., "api", "voice", "webhook")
            message_id: External message ID (optional, for webhook deduplication)

        Uses hash to keep keys small and consistent.
        """
        # Build key from all available components (Clawdbot pattern)
        # Pattern: [channel, user_id, session_id, message_id, message].filter(Boolean).join("|")
        parts = [
            channel or "",
            user_id,
            session_id or "",
            message_id or "",
            message
        ]
        # Filter empty strings and join
        content = "|".join(p for p in parts if p)
        return hashlib.sha256(content.encode()).hexdigest()[:32]

    def check(
        self,
        user_id: str,
        message: str,
        session_id: Optional[str] = None,
        channel: Optional[str] = None,
        message_id: Optional[str] = None
    ) -> bool:
        """
        Check if message is a duplicate.

        Returns True if this exact message from this user was seen recently.
        Also adds the message to the cache if it's new.

        From Clawdbot pattern: uses multiple identifiers for comprehensive deduplication.

        Args:
            user_id: User identifier
            message: Message content
            session_id: Session identifier (optional)
            channel: Channel/provider (optional)
            message_id: External message ID (optional, for webhooks)

        Returns:
            True if duplicate (should be rejected), False if new
        """
        if not message:
            return False

        key = self._make_key(user_id, message, session_id, channel, message_id)
        now = time()

        # Check existing
        if key in self._cache:
            ts = self._cache[key]
            if now - ts < self._ttl:
                # Still valid - this is a duplicate
                # Touch (move to end for LRU)
                self._cache.move_to_end(key)
                self._cache[key] = now
                logger.warning(
                    f"Duplicate inbound message detected for user {user_id[:8]}...: "
                    f"{message[:30]}..."
                )
                return True  # DUPLICATE

        # Add new entry
        self._cache[key] = now
        self._cache.move_to_end(key)

        # Prune expired and over-limit
        self._prune(now)

        return False  # NEW

    def _prune(self, now: float) -> None:
        """Remove expired entries and enforce max size."""
        # Remove expired
        cutoff = now - self._ttl
        to_remove = [k for k, ts in self._cache.items() if ts < cutoff]
        for k in to_remove:
            del self._cache[k]

        # Remove oldest if over limit (LRU eviction)
        while len(self._cache) > self._max_size:
            self._cache.popitem(last=False)

    def clear(self) -> None:
        """Clear all cached entries."""
        self._cache.clear()

    def size(self) -> int:
        """Get current cache size."""
        return len(self._cache)


# Global instance for easy access
_inbound_dedupe_cache: Optional[InboundDedupeCache] = None


def get_inbound_dedupe_cache() -> InboundDedupeCache:
    """Get or create the global inbound dedupe cache."""
    global _inbound_dedupe_cache
    if _inbound_dedupe_cache is None:
        _inbound_dedupe_cache = InboundDedupeCache()
    return _inbound_dedupe_cache


def is_duplicate_inbound(
    user_id: str,
    message: str,
    session_id: Optional[str] = None,
    channel: Optional[str] = None,
    message_id: Optional[str] = None
) -> bool:
    """
    Check if an inbound message is a duplicate.

    Convenience function using the global cache.
    From Clawdbot pattern: supports multiple identifiers for comprehensive deduplication.

    Args:
        user_id: User identifier
        message: Message content
        session_id: Session identifier (optional)
        channel: Channel/provider (optional, e.g., "api", "voice", "webhook")
        message_id: External message ID (optional, for webhook deduplication)

    Returns:
        True if duplicate (should be rejected), False if new
    """
    return get_inbound_dedupe_cache().check(
        user_id, message, session_id, channel, message_id
    )
