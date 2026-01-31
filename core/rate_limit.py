"""
Rate Limiting - Prevents API Abuse

From Clawdbot patterns - implements per-user and per-session rate limits
to prevent abuse and ensure fair usage.

Uses token bucket algorithm for smooth rate limiting.
"""
import logging
from typing import Dict, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import time
from collections import OrderedDict
import asyncio

from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
import json

logger = logging.getLogger("brainmap.rate_limit")

# Default rate limits
DEFAULT_REQUESTS_PER_MINUTE = 30  # Per user
DEFAULT_REQUESTS_PER_SESSION_MINUTE = 10  # Per session
DEFAULT_BURST_SIZE = 5  # Allow small bursts
RATE_LIMIT_CACHE_TTL = 3600  # 1 hour cache cleanup
RATE_LIMIT_CACHE_MAX_SIZE = 10000


@dataclass
class TokenBucket:
    """Token bucket for rate limiting."""
    tokens: float
    last_update: float
    max_tokens: float
    refill_rate: float  # tokens per second

    def consume(self, tokens: int = 1) -> Tuple[bool, float]:
        """
        Try to consume tokens from the bucket.

        Returns:
            Tuple of (allowed, wait_time_seconds)
            - allowed: True if request is allowed
            - wait_time: Seconds to wait if not allowed (0 if allowed)
        """
        now = time()

        # Refill tokens based on time passed
        elapsed = now - self.last_update
        self.tokens = min(self.max_tokens, self.tokens + elapsed * self.refill_rate)
        self.last_update = now

        if self.tokens >= tokens:
            self.tokens -= tokens
            return True, 0.0
        else:
            # Calculate wait time
            needed = tokens - self.tokens
            wait_time = needed / self.refill_rate
            return False, wait_time


class RateLimiter:
    """
    Rate limiter with per-user and per-session limits.

    From Clawdbot patterns - prevents abuse while allowing legitimate usage.
    """

    def __init__(
        self,
        requests_per_minute: int = DEFAULT_REQUESTS_PER_MINUTE,
        session_requests_per_minute: int = DEFAULT_REQUESTS_PER_SESSION_MINUTE,
        burst_size: int = DEFAULT_BURST_SIZE,
        cache_ttl: int = RATE_LIMIT_CACHE_TTL,
        cache_max_size: int = RATE_LIMIT_CACHE_MAX_SIZE
    ):
        """
        Initialize rate limiter.

        Args:
            requests_per_minute: Max requests per user per minute
            session_requests_per_minute: Max requests per session per minute
            burst_size: Allow this many requests in immediate burst
            cache_ttl: Seconds before cleaning up old entries
            cache_max_size: Max entries to track
        """
        self._user_buckets: OrderedDict[str, TokenBucket] = OrderedDict()
        self._session_buckets: OrderedDict[str, TokenBucket] = OrderedDict()

        self._user_rate = requests_per_minute / 60.0  # tokens per second
        self._session_rate = session_requests_per_minute / 60.0
        self._burst_size = burst_size
        self._cache_ttl = cache_ttl
        self._cache_max_size = cache_max_size
        self._last_cleanup = time()

    def _get_user_bucket(self, user_id: str) -> TokenBucket:
        """Get or create a token bucket for a user."""
        if user_id not in self._user_buckets:
            self._user_buckets[user_id] = TokenBucket(
                tokens=self._burst_size,
                last_update=time(),
                max_tokens=self._burst_size,
                refill_rate=self._user_rate
            )
        # Move to end for LRU
        self._user_buckets.move_to_end(user_id)
        return self._user_buckets[user_id]

    def _get_session_bucket(self, session_id: str) -> TokenBucket:
        """Get or create a token bucket for a session."""
        if session_id not in self._session_buckets:
            self._session_buckets[session_id] = TokenBucket(
                tokens=self._burst_size,
                last_update=time(),
                max_tokens=self._burst_size,
                refill_rate=self._session_rate
            )
        # Move to end for LRU
        self._session_buckets.move_to_end(session_id)
        return self._session_buckets[session_id]

    def check(
        self,
        user_id: str,
        session_id: Optional[str] = None
    ) -> Tuple[bool, float, str]:
        """
        Check if request is allowed.

        Args:
            user_id: User identifier
            session_id: Session identifier (optional)

        Returns:
            Tuple of (allowed, wait_time, limit_type)
            - allowed: True if request is allowed
            - wait_time: Seconds to wait if not allowed
            - limit_type: Which limit was hit ("user", "session", or "")
        """
        # Clean up periodically
        now = time()
        if now - self._last_cleanup > 60:  # Every minute
            self._cleanup()
            self._last_cleanup = now

        # Check user limit first
        user_bucket = self._get_user_bucket(user_id)
        allowed, wait_time = user_bucket.consume()
        if not allowed:
            logger.warning(f"User rate limit hit: {user_id}, wait: {wait_time:.1f}s")
            return False, wait_time, "user"

        # Check session limit if provided
        if session_id:
            session_bucket = self._get_session_bucket(session_id)
            allowed, wait_time = session_bucket.consume()
            if not allowed:
                # Refund user token since we're rejecting at session level
                user_bucket.tokens = min(user_bucket.max_tokens, user_bucket.tokens + 1)
                logger.warning(f"Session rate limit hit: {session_id}, wait: {wait_time:.1f}s")
                return False, wait_time, "session"

        return True, 0.0, ""

    def _cleanup(self) -> None:
        """Remove old entries from caches."""
        now = time()
        cutoff = now - self._cache_ttl

        # Clean user buckets
        to_remove = [
            k for k, v in self._user_buckets.items()
            if v.last_update < cutoff
        ]
        for k in to_remove:
            del self._user_buckets[k]

        # Clean session buckets
        to_remove = [
            k for k, v in self._session_buckets.items()
            if v.last_update < cutoff
        ]
        for k in to_remove:
            del self._session_buckets[k]

        # Enforce max size (LRU eviction)
        while len(self._user_buckets) > self._cache_max_size:
            self._user_buckets.popitem(last=False)
        while len(self._session_buckets) > self._cache_max_size:
            self._session_buckets.popitem(last=False)

    def get_stats(self) -> Dict[str, int]:
        """Get rate limiter statistics."""
        return {
            "user_buckets": len(self._user_buckets),
            "session_buckets": len(self._session_buckets)
        }


# Global rate limiter instance
_rate_limiter: Optional[RateLimiter] = None


def get_rate_limiter() -> RateLimiter:
    """Get the global rate limiter instance."""
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter()
    return _rate_limiter


def check_rate_limit(
    user_id: str,
    session_id: Optional[str] = None
) -> Tuple[bool, float, str]:
    """
    Convenience function to check rate limit.

    Args:
        user_id: User identifier
        session_id: Session identifier (optional)

    Returns:
        Tuple of (allowed, wait_time, limit_type)
    """
    return get_rate_limiter().check(user_id, session_id)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    FastAPI middleware for rate limiting.

    Automatically rate limits based on user ID from request.
    """

    def __init__(self, app, user_header: str = "X-User-ID"):
        super().__init__(app)
        self._limiter = get_rate_limiter()
        self._user_header = user_header

    async def dispatch(self, request: Request, call_next):
        # Extract user ID from header or default
        user_id = request.headers.get(self._user_header, "anonymous")

        # Extract session ID from request if available
        session_id = None
        if request.method == "POST":
            # Try query param first
            session_id = request.query_params.get("session_id")
            if not session_id:
                # Peek JSON body (for /v1/chat/send) and restore it for downstream
                try:
                    body = await request.body()
                    if body:
                        try:
                            payload = json.loads(body.decode("utf-8"))
                            session_id = payload.get("session_id")
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            session_id = None
                    # Restore body for downstream handlers
                    request._body = body  # type: ignore[attr-defined]
                except Exception:
                    session_id = None

        # Check rate limit
        allowed, wait_time, limit_type = self._limiter.check(user_id, session_id)

        if not allowed:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "rate_limit_exceeded",
                    "message": f"Too many requests. Please wait {wait_time:.1f} seconds.",
                    "limit_type": limit_type,
                    "retry_after": wait_time
                },
                headers={"Retry-After": str(int(wait_time) + 1)}
            )

        return await call_next(request)
