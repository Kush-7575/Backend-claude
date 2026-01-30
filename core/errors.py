"""
Error Classification - Port from Clawdbot

Classifies errors to determine retry strategy:
- Context overflow: Progressive compaction retry
- Rate limit: Exponential backoff
- Auth errors: Fail fast
- Timeout: Simple retry
- Billing: Fail with message

Based on Clawdbot's pi-embedded-helpers/errors.ts
"""
from enum import Enum
import re
from typing import Optional


class ErrorType(Enum):
    """Error types for classification."""
    CONTEXT_OVERFLOW = "context_overflow"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    BILLING = "billing"
    AUTH = "auth"
    OVERLOADED = "overloaded"
    FORMAT = "format"
    UNKNOWN = "unknown"


# Error patterns from Clawdbot errors.ts
OVERFLOW_PATTERNS = [
    r'prompt is too long',
    r'input is too long for requested model',
    r'exceeds the context window',
    r'input token count.*exceeds the maximum',
    r'maximum prompt length is \d+',
    r'reduce the length of the messages',
    r'maximum context length is \d+ tokens',
    r'exceeds the limit of \d+',
    r'exceeds the available context size',
    r'greater than the context length',
    r'context[_ ]length[_ ]exceeded',
    r'too many tokens',
    r'token limit exceeded',
    r'request_too_large',
]

RATE_LIMIT_PATTERNS = [
    r'rate[_ ]limit',
    r'too many requests',
    r'429',
    r'exceeded quota',
]

AUTH_PATTERNS = [
    r'invalid[_ ]?api[_ ]?key',
    r'unauthorized',
    r'401',
    r'403',
]

TIMEOUT_PATTERNS = [
    r'timeout',
    r'timed out',
    r'deadline exceeded',
]

BILLING_PATTERNS = [
    r'402',
    r'payment required',
    r'billing',
    r'insufficient funds',
]


def classify_error(error: Exception) -> ErrorType:
    """
    Classify error type from exception (from Clawdbot).

    Args:
        error: The exception to classify

    Returns:
        ErrorType indicating the error category
    """
    msg = str(error).lower()

    for pattern in OVERFLOW_PATTERNS:
        if re.search(pattern, msg, re.IGNORECASE):
            return ErrorType.CONTEXT_OVERFLOW

    for pattern in RATE_LIMIT_PATTERNS:
        if re.search(pattern, msg, re.IGNORECASE):
            return ErrorType.RATE_LIMIT

    for pattern in AUTH_PATTERNS:
        if re.search(pattern, msg, re.IGNORECASE):
            return ErrorType.AUTH

    for pattern in TIMEOUT_PATTERNS:
        if re.search(pattern, msg, re.IGNORECASE):
            return ErrorType.TIMEOUT

    for pattern in BILLING_PATTERNS:
        if re.search(pattern, msg, re.IGNORECASE):
            return ErrorType.BILLING

    if 'overloaded' in msg:
        return ErrorType.OVERLOADED

    return ErrorType.UNKNOWN


def get_user_friendly_message(error_type: ErrorType) -> str:
    """
    Get user-friendly error message for display.

    Args:
        error_type: The classified error type

    Returns:
        Human-readable error message
    """
    messages = {
        ErrorType.CONTEXT_OVERFLOW: "Our conversation got quite long. Let me summarize and continue...",
        ErrorType.RATE_LIMIT: "I'm receiving too many requests right now. Please wait a moment.",
        ErrorType.TIMEOUT: "The request took too long. Let me try again.",
        ErrorType.BILLING: "There's a billing issue with the AI service. Please check your account.",
        ErrorType.AUTH: "There's an authentication issue. Please check the API key.",
        ErrorType.OVERLOADED: "The AI service is currently overloaded. Please try again shortly.",
        ErrorType.FORMAT: "There was a formatting issue with the request.",
        ErrorType.UNKNOWN: "Something went wrong. Please try again.",
    }
    return messages.get(error_type, messages[ErrorType.UNKNOWN])


def is_retryable(error_type: ErrorType) -> bool:
    """
    Check if error type is retryable.

    Args:
        error_type: The classified error type

    Returns:
        True if the error can be retried
    """
    retryable = {
        ErrorType.CONTEXT_OVERFLOW,  # Retry with compaction
        ErrorType.RATE_LIMIT,        # Retry with backoff
        ErrorType.TIMEOUT,           # Simple retry
        ErrorType.OVERLOADED,        # Retry with backoff
    }
    return error_type in retryable


class CompactionFailedError(Exception):
    """Raised when compaction cannot reduce context enough."""
    pass


class RateLimitError(Exception):
    """Raised when rate limited."""
    pass


class AuthenticationError(Exception):
    """Raised on authentication failures."""
    pass
