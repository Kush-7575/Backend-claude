"""
Model Failover - Automatic retry and fallback on API failures.

Adapted from Clawdbot's failover-error.ts and model-fallback.ts

Provides:
1. FailoverError with reason tracking
2. Model fallback configuration
3. Retry with exponential backoff
4. Auth profile rotation (future)

This handles transient failures gracefully while failing fast
on unrecoverable errors.
"""
import logging
import asyncio
from enum import Enum
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Callable, Awaitable
from datetime import datetime, timezone

from core.errors import classify_error, ErrorType

logger = logging.getLogger("brainmap.failover")


class FailoverReason(Enum):
    """Reasons for triggering failover (from Clawdbot)."""
    BILLING = "billing"
    RATE_LIMIT = "rate_limit"
    AUTH = "auth"
    TIMEOUT = "timeout"
    FORMAT = "format"
    CONTEXT_OVERFLOW = "context_overflow"
    OVERLOADED = "overloaded"
    UNKNOWN = "unknown"


@dataclass
class FailoverError(Exception):
    """
    Error with failover metadata for retry/fallback decisions.
    
    From Clawdbot's failover-error.ts FailoverError class.
    """
    message: str
    reason: FailoverReason
    provider: Optional[str] = None
    model: Optional[str] = None
    profile_id: Optional[str] = None
    status: Optional[int] = None
    code: Optional[str] = None
    cause: Optional[Exception] = None
    
    def __str__(self) -> str:
        return f"{self.message} (reason={self.reason.value})"
    
    def __post_init__(self):
        super().__init__(self.message)


def error_type_to_failover_reason(error_type: ErrorType) -> FailoverReason:
    """Convert ErrorType to FailoverReason."""
    mapping = {
        ErrorType.CONTEXT_OVERFLOW: FailoverReason.CONTEXT_OVERFLOW,
        ErrorType.RATE_LIMIT: FailoverReason.RATE_LIMIT,
        ErrorType.AUTH: FailoverReason.AUTH,
        ErrorType.TIMEOUT: FailoverReason.TIMEOUT,
        ErrorType.BILLING: FailoverReason.BILLING,
        ErrorType.OVERLOADED: FailoverReason.OVERLOADED,
        ErrorType.FORMAT: FailoverReason.FORMAT,
    }
    return mapping.get(error_type, FailoverReason.UNKNOWN)


def coerce_to_failover_error(
    err: Exception,
    provider: Optional[str] = None,
    model: Optional[str] = None
) -> FailoverError:
    """
    Convert any exception to a FailoverError.
    
    From Clawdbot's failover-error.ts coerceToFailoverError.
    """
    if isinstance(err, FailoverError):
        return err
    
    error_type = classify_error(err)
    reason = error_type_to_failover_reason(error_type)
    
    return FailoverError(
        message=str(err),
        reason=reason,
        provider=provider,
        model=model,
        cause=err
    )


def should_retry(reason: FailoverReason) -> bool:
    """
    Check if error reason is retryable.
    
    From Clawdbot patterns.
    """
    retryable = {
        FailoverReason.RATE_LIMIT,
        FailoverReason.TIMEOUT,
        FailoverReason.OVERLOADED,
    }
    return reason in retryable


def should_failover(reason: FailoverReason) -> bool:
    """
    Check if error should trigger model failover.
    
    From Clawdbot patterns.
    """
    failover_reasons = {
        FailoverReason.BILLING,
        FailoverReason.AUTH,
        FailoverReason.FORMAT,
        FailoverReason.CONTEXT_OVERFLOW,
    }
    return reason in failover_reasons


@dataclass
class ModelCandidate:
    """A model candidate for failover."""
    provider: str
    model: str


@dataclass
class FallbackAttempt:
    """Record of a failback attempt."""
    provider: str
    model: str
    error: str
    reason: Optional[FailoverReason] = None
    status: Optional[int] = None


@dataclass
class FallbackConfig:
    """Configuration for model fallback."""
    primary_provider: str = "anthropic"
    primary_model: str = "claude-sonnet-4-20250514"
    fallbacks: List[ModelCandidate] = field(default_factory=list)
    max_retries: int = 3
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 30.0


def get_exponential_delay(
    attempt: int,
    base_delay: float = 1.0,
    max_delay: float = 30.0
) -> float:
    """Calculate exponential backoff delay with jitter."""
    import random
    delay = min(base_delay * (2 ** attempt), max_delay)
    # Add jitter: 0.5x to 1.5x
    jitter = random.uniform(0.5, 1.5)
    return delay * jitter


async def with_retry_and_fallback(
    operation: Callable[[], Awaitable[Any]],
    config: FallbackConfig,
    on_retry: Optional[Callable[[int, FailoverError], Awaitable[None]]] = None,
    on_fallback: Optional[Callable[[ModelCandidate, FailoverError], Awaitable[Any]]] = None
) -> Any:
    """
    Execute operation with retry and fallback logic.
    
    From Clawdbot's model-fallback.ts patterns.
    
    Args:
        operation: The async operation to execute
        config: Fallback configuration
        on_retry: Called before each retry
        on_fallback: Called when falling back to another model
    
    Returns:
        Result from successful operation
    
    Raises:
        FailoverError: When all retries and fallbacks exhausted
    """
    attempts: List[FallbackAttempt] = []
    last_error: Optional[FailoverError] = None
    
    # Try primary model with retries
    for attempt in range(config.max_retries):
        try:
            return await operation()
        except Exception as e:
            failover_err = coerce_to_failover_error(
                e if isinstance(e, Exception) else Exception(str(e)),
                config.primary_provider,
                config.primary_model
            )
            last_error = failover_err
            
            attempts.append(FallbackAttempt(
                provider=config.primary_provider,
                model=config.primary_model,
                error=str(e),
                reason=failover_err.reason
            ))
            
            if not should_retry(failover_err.reason):
                logger.info(f"Error not retryable: {failover_err.reason.value}")
                break
            
            if attempt < config.max_retries - 1:
                delay = get_exponential_delay(
                    attempt,
                    config.base_delay_seconds,
                    config.max_delay_seconds
                )
                logger.info(f"Retry {attempt + 1}/{config.max_retries} after {delay:.1f}s")
                
                if on_retry:
                    await on_retry(attempt, failover_err)
                
                await asyncio.sleep(delay)
    
    # Try fallback models
    if last_error and should_failover(last_error.reason):
        for fallback in config.fallbacks:
            try:
                logger.info(f"Failing over to {fallback.provider}/{fallback.model}")
                
                if on_fallback:
                    return await on_fallback(fallback, last_error)
                else:
                    # No fallback handler, can't switch models
                    break
                    
            except Exception as e:
                failover_err = coerce_to_failover_error(
                    e if isinstance(e, Exception) else Exception(str(e)),
                    fallback.provider,
                    fallback.model
                )
                attempts.append(FallbackAttempt(
                    provider=fallback.provider,
                    model=fallback.model,
                    error=str(e),
                    reason=failover_err.reason
                ))
                last_error = failover_err
    
    # All attempts failed
    if last_error:
        logger.error(f"All failover attempts exhausted: {attempts}")
        raise last_error
    
    raise FailoverError(
        message="Operation failed with unknown error",
        reason=FailoverReason.UNKNOWN
    )


@dataclass
class CompactionRetryConfig:
    """Configuration for compaction retry."""
    max_attempts: int = 3
    wait_between_ms: int = 1000


async def with_compaction_retry(
    operation: Callable[[], Awaitable[Any]],
    compact_fn: Callable[[], Awaitable[None]],
    config: Optional[CompactionRetryConfig] = None
) -> Any:
    """
    Execute operation with compaction retry on context overflow.
    
    From Clawdbot's pi-embedded-subscribe.ts waitsMultipleCompactionRetries.
    
    If operation fails with context overflow, runs compaction and retries.
    
    Args:
        operation: The async operation to execute
        compact_fn: Function to run compaction
        config: Retry configuration
    
    Returns:
        Result from successful operation
    
    Raises:
        FailoverError: When all compaction retries exhausted
    """
    config = config or CompactionRetryConfig()
    
    for attempt in range(config.max_attempts):
        try:
            return await operation()
        except Exception as e:
            error_type = classify_error(e)
            
            if error_type != ErrorType.CONTEXT_OVERFLOW:
                raise
            
            if attempt >= config.max_attempts - 1:
                logger.error(f"Context overflow after {config.max_attempts} compaction attempts")
                raise FailoverError(
                    message=f"Context overflow after {config.max_attempts} compaction attempts: {e}",
                    reason=FailoverReason.CONTEXT_OVERFLOW,
                    cause=e
                )
            
            logger.warning(f"Context overflow, running compaction (attempt {attempt + 1})")
            await compact_fn()
            
            if config.wait_between_ms > 0:
                await asyncio.sleep(config.wait_between_ms / 1000)
