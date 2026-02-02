"""
Context Manager - Token Counting and Context Window Management

This is the CRITICAL component that prevents session overflow.

Key responsibilities:
1. Count tokens in messages using tiktoken
2. Track total context usage
3. Trigger compaction when approaching limits
4. Coordinate with Memory Manager for pre-compaction flush
5. Pre-flight context window guard (from Clawdbot)

Performance optimizations:
- Token count memoization with LRU cache (Clawdbot pattern)
"""
import logging
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
import tiktoken

from core.config import settings
from core.failover import FailoverError, FailoverReason

logger = logging.getLogger("brainmap.context")


# =============================================================================
# Token Count Cache (Clawdbot pattern - memoize expensive tokenization)
# =============================================================================

# Global tiktoken encoding for memoized function
_global_encoding = None

def _get_global_encoding():
    """Get or create global tiktoken encoding."""
    global _global_encoding
    if _global_encoding is None:
        try:
            _global_encoding = tiktoken.get_encoding("cl100k_base")
        except Exception as e:
            logger.warning(f"Failed to load tiktoken: {e}")
    return _global_encoding


@lru_cache(maxsize=512)
def _count_tokens_cached(text: str) -> int:
    """
    Memoized token counting (Clawdbot pattern).
    
    Caches up to 512 unique text strings to avoid redundant
    tokenization of the same content (e.g., system prompts,
    repeated message blocks).
    """
    if not text:
        return 0
    
    encoding = _get_global_encoding()
    if encoding:
        return len(encoding.encode(text))
    else:
        # Rough approximation: ~4 chars per token
        return len(text) // 4


def clear_token_cache() -> None:
    """Clear the token count cache (for testing)."""
    _count_tokens_cached.cache_clear()


def get_token_cache_info():
    """Get cache statistics for diagnostics."""
    return _count_tokens_cached.cache_info()


# Context window guard constants (from Clawdbot context-window-guard.ts)
CONTEXT_WINDOW_WARN_BELOW_TOKENS = 16000  # Warn if context window is below this
CONTEXT_WINDOW_HARD_MIN_TOKENS = 8000     # Block if context window is below this


class ContextWindowSource(str, Enum):
    """Source of context window info."""
    MODEL_DEFAULT = "model_default"
    CONFIG_OVERRIDE = "config_override"
    PROVIDER_DEFAULT = "provider_default"


@dataclass
class ContextWindowInfo:
    """Context window configuration info."""
    tokens: int
    source: ContextWindowSource
    provider: str
    model_id: str
    model_context_window: Optional[int] = None


@dataclass
class ContextWindowGuard:
    """Result of context window guard evaluation."""
    tokens: int
    source: ContextWindowSource
    should_warn: bool
    should_block: bool
    warn_message: Optional[str] = None
    block_message: Optional[str] = None


def resolve_context_window_info(
    provider: str,
    model_id: str,
    model_context_window: Optional[int] = None,
    config_override: Optional[int] = None,
    default_tokens: int = 200000
) -> ContextWindowInfo:
    """
    Resolve context window info from various sources.
    
    Priority:
    1. Config override (if set)
    2. Model's advertised context window
    3. Default fallback
    
    From Clawdbot's resolveContextWindowInfo.
    """
    # Check config override first
    if config_override and config_override > 0:
        return ContextWindowInfo(
            tokens=config_override,
            source=ContextWindowSource.CONFIG_OVERRIDE,
            provider=provider,
            model_id=model_id,
            model_context_window=model_context_window
        )
    
    # Use model's context window if available
    if model_context_window and model_context_window > 0:
        return ContextWindowInfo(
            tokens=model_context_window,
            source=ContextWindowSource.MODEL_DEFAULT,
            provider=provider,
            model_id=model_id,
            model_context_window=model_context_window
        )
    
    # Fallback to default
    return ContextWindowInfo(
        tokens=default_tokens,
        source=ContextWindowSource.PROVIDER_DEFAULT,
        provider=provider,
        model_id=model_id,
        model_context_window=model_context_window
    )


def evaluate_context_window_guard(
    info: ContextWindowInfo,
    warn_below_tokens: int = CONTEXT_WINDOW_WARN_BELOW_TOKENS,
    hard_min_tokens: int = CONTEXT_WINDOW_HARD_MIN_TOKENS
) -> ContextWindowGuard:
    """
    Evaluate whether to warn or block based on context window.
    
    From Clawdbot's evaluateContextWindowGuard.
    """
    should_warn = info.tokens < warn_below_tokens
    should_block = info.tokens < hard_min_tokens
    
    warn_message = None
    block_message = None
    
    if should_warn:
        warn_message = (
            f"Low context window: {info.provider}/{info.model_id} "
            f"ctx={info.tokens} (warn<{warn_below_tokens}) source={info.source.value}"
        )
    
    if should_block:
        block_message = (
            f"Model context window too small ({info.tokens} tokens). "
            f"Minimum is {hard_min_tokens}."
        )
    
    return ContextWindowGuard(
        tokens=info.tokens,
        source=info.source,
        should_warn=should_warn,
        should_block=should_block,
        warn_message=warn_message,
        block_message=block_message
    )


def check_context_window_guard(
    provider: str,
    model_id: str,
    model_context_window: Optional[int] = None,
    config_override: Optional[int] = None,
) -> ContextWindowGuard:
    """
    Check context window guard and return result.
    
    Logs warnings but does NOT throw - caller decides what to do.
    """
    info = resolve_context_window_info(
        provider=provider,
        model_id=model_id,
        model_context_window=model_context_window,
        config_override=config_override
    )
    
    guard = evaluate_context_window_guard(info)
    
    if guard.should_warn and guard.warn_message:
        logger.warning(guard.warn_message)
    
    return guard


def enforce_context_window_guard(
    provider: str,
    model_id: str,
    model_context_window: Optional[int] = None,
    config_override: Optional[int] = None,
) -> None:
    """
    Check context window guard and THROW if blocked.
    
    Use this as a pre-flight check before starting agent runs.
    
    Raises:
        FailoverError: If context window is too small
    """
    guard = check_context_window_guard(
        provider=provider,
        model_id=model_id,
        model_context_window=model_context_window,
        config_override=config_override
    )
    
    if guard.should_block:
        logger.error(
            f"Blocked model (context window too small): {provider}/{model_id} "
            f"ctx={guard.tokens} (min={CONTEXT_WINDOW_HARD_MIN_TOKENS}) "
            f"source={guard.source.value}"
        )
        raise FailoverError(
            message=guard.block_message or "Context window too small",
            reason=FailoverReason.UNKNOWN,
            provider=provider,
            model=model_id
        )


@dataclass
class ContextMetrics:
    """Metrics for current context state."""
    total_tokens: int
    message_count: int
    max_tokens: int
    threshold_tokens: int
    usage_percent: float
    needs_compaction: bool


# Multi-part compaction constants (from Clawdbot compaction.ts)
BASE_CHUNK_RATIO = 0.4   # Base ratio for splitting messages
MIN_CHUNK_RATIO = 0.15   # Minimum chunk ratio
SAFETY_MARGIN = 1.2      # 20% buffer for token estimation inaccuracy
DEFAULT_PARTS = 2        # Default number of parts for chunking
MAX_TOKENS_PER_CHUNK = 50000  # Max tokens per summarization chunk


class ContextManager:
    """
    Manages context window for Claude conversations.
    
    This is based on Clawdbot's compaction pattern - before the context
    window fills up, we:
    1. Flush important information to durable memory
    2. Summarize old messages
    3. Keep only recent context
    
    This prevents the "context overflow" problem where sessions fail
    after enough messages.
    """
    
    # Claude model context limits
    MODEL_LIMITS = {
        "claude-sonnet-4-20250514": 200000,
        "claude-3-5-sonnet-20241022": 200000,
        "claude-3-opus-20240229": 200000,
        "claude-3-haiku-20240307": 200000,
    }
    
    def __init__(self):
        """Initialize context manager with tiktoken encoder."""
        # Use cl100k_base encoding (compatible with Claude)
        try:
            self._encoding = tiktoken.get_encoding("cl100k_base")
        except Exception as e:
            logger.warning(f"Failed to load tiktoken: {e}. Using approximate counting.")
            self._encoding = None
        
        self._max_tokens = settings.MAX_CONTEXT_TOKENS
        self._reserve_tokens = settings.RESERVE_RESPONSE_TOKENS
        self._keep_recent = settings.KEEP_RECENT_TOKENS
        self._threshold = settings.COMPACTION_THRESHOLD
    
    def count_tokens(self, text: str) -> int:
        """
        Count tokens in a text string.
        
        Uses memoized token counting for performance (Clawdbot pattern).
        Caches results to avoid redundant tokenization of repeated content.
        """
        return _count_tokens_cached(text)

    def truncate_text_to_tokens(self, text: str, max_tokens: int) -> str:
        """Truncate text to a maximum number of tokens."""
        if max_tokens <= 0:
            return ""
        if not text:
            return ""
        if self._encoding:
            try:
                tokens = self._encoding.encode(text)
                if len(tokens) <= max_tokens:
                    return text
                truncated = self._encoding.decode(tokens[:max_tokens])
                return truncated
            except Exception:
                pass
        # Fallback approximate by chars
        return text[: max_tokens * 4]

    def soft_trim_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Soft-trim oversized message blocks with head+tail preservation."""
        max_chars = settings.CONTEXT_SOFT_TRIM_MAX_CHARS
        if max_chars <= 0:
            return messages

        head_ratio = settings.CONTEXT_SOFT_TRIM_HEAD_RATIO
        tail_ratio = settings.CONTEXT_SOFT_TRIM_TAIL_RATIO
        head_ratio = max(0.0, min(1.0, head_ratio))
        tail_ratio = max(0.0, min(1.0, tail_ratio))
        if head_ratio + tail_ratio > 1.0:
            tail_ratio = max(0.0, 1.0 - head_ratio)

        def trim_text(value: str) -> str:
            if len(value) <= max_chars:
                return value
            head_chars = int(max_chars * head_ratio)
            tail_chars = int(max_chars * tail_ratio)
            head = value[:head_chars] if head_chars > 0 else ""
            tail = value[-tail_chars:] if tail_chars > 0 else ""
            marker = "\n...[TRUNCATED]...\n"
            return f"{head}{marker}{tail}".strip()

        trimmed: List[Dict[str, Any]] = []
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                new_msg = dict(msg)
                new_msg["content"] = trim_text(content)
                trimmed.append(new_msg)
                continue
            if isinstance(content, list):
                new_msg = dict(msg)
                new_blocks = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        new_block = dict(block)
                        new_block["text"] = trim_text(block.get("text", ""))
                        new_blocks.append(new_block)
                    else:
                        new_blocks.append(block)
                new_msg["content"] = new_blocks
                trimmed.append(new_msg)
                continue
            trimmed.append(msg)

        return trimmed
    
    def count_message_tokens(self, message: Dict[str, Any]) -> int:
        """
        Count tokens in a message dict.
        
        Handles both simple text and complex content blocks.
        """
        tokens = 0
        
        # Role token (approximate)
        tokens += 4
        
        content = message.get("content", "")
        
        if isinstance(content, str):
            tokens += self.count_tokens(content)
        elif isinstance(content, list):
            # Content blocks (text, images, etc.)
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        tokens += self.count_tokens(block.get("text", ""))
                    elif block.get("type") == "image":
                        # Images are ~1000 tokens (approximation)
                        tokens += 1000
                    elif block.get("type") == "tool_use":
                        tokens += self.count_tokens(str(block.get("input", {})))
                    elif block.get("type") == "tool_result":
                        tokens += self.count_tokens(str(block.get("content", "")))
        
        return tokens
    
    def count_messages_tokens(self, messages: List[Dict[str, Any]]) -> int:
        """Count total tokens across all messages."""
        return sum(self.count_message_tokens(msg) for msg in messages)
    
    def get_metrics(self, messages: List[Dict[str, Any]]) -> ContextMetrics:
        """
        Get current context metrics.
        
        Use this to monitor context usage and decide when to compact.
        """
        total_tokens = self.count_messages_tokens(messages)
        threshold_tokens = int(self._max_tokens * self._threshold)
        
        return ContextMetrics(
            total_tokens=total_tokens,
            message_count=len(messages),
            max_tokens=self._max_tokens,
            threshold_tokens=threshold_tokens,
            usage_percent=(total_tokens / self._max_tokens) * 100,
            needs_compaction=total_tokens > threshold_tokens
        )
    
    def needs_compaction(self, messages: List[Dict[str, Any]]) -> bool:
        """Check if messages need compaction."""
        return self.get_metrics(messages).needs_compaction
    
    def estimate_available_tokens(self, messages: List[Dict[str, Any]]) -> int:
        """
        Estimate available tokens for the next response.
        
        Returns available tokens after accounting for:
        - Current messages
        - Reserved response tokens
        """
        used = self.count_messages_tokens(messages)
        return max(0, self._max_tokens - used - self._reserve_tokens)
    
    async def prepare_for_compaction(
        self,
        messages: List[Dict[str, Any]],
        memory_manager: Optional[Any] = None
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
        """
        Prepare messages for compaction.

        This is the pre-compaction step that:
        1. Identifies important information to save
        2. Calls memory manager to persist if available
        3. Returns messages split into [to_summarize, to_keep]

        Returns:
            Tuple of (messages_to_summarize, messages_to_keep, metadata)
        """
        metrics = self.get_metrics(messages)
        
        if not metrics.needs_compaction:
            return [], messages, {"compacted": False}
        
        logger.info(
            f"Preparing compaction: {metrics.total_tokens} tokens "
            f"({metrics.usage_percent:.1f}% of {self._max_tokens})"
        )
        
        # Find split point - keep recent messages up to KEEP_RECENT_TOKENS
        tokens_from_end = 0
        split_index = len(messages)
        
        for i in range(len(messages) - 1, -1, -1):
            msg_tokens = self.count_message_tokens(messages[i])
            if tokens_from_end + msg_tokens > self._keep_recent:
                split_index = i + 1
                break
            tokens_from_end += msg_tokens
        
        to_summarize = messages[:split_index]
        to_keep = messages[split_index:]

        # Flush to memory if manager available - VERIFY SUCCESS before continuing
        memory_flush_success = False
        if memory_manager and to_summarize:
            logger.info(f"Flushing {len(to_summarize)} messages to memory before compaction")
            try:
                flush_result = await memory_manager.flush_messages(to_summarize)
                memory_flush_success = flush_result.get("success", False)
                if not memory_flush_success:
                    logger.error(
                        f"Memory flush failed! Errors: {flush_result.get('errors', [])}. "
                        "Proceeding with compaction but data may be lost."
                    )
                else:
                    logger.info(
                        f"Memory flush verified: {flush_result.get('facts_extracted', 0)} facts, "
                        f"{flush_result.get('daily_log_entries', 0)} log entries"
                    )
            except Exception as e:
                logger.error(f"Memory flush threw exception: {e}. Proceeding with compaction.")
        elif to_summarize:
            logger.warning("No memory manager available - messages will be summarized without backup!")

        return to_summarize, to_keep, {
            "compacted": True,
            "summarized_count": len(to_summarize),
            "kept_count": len(to_keep),
            "saved_tokens": self.count_messages_tokens(to_summarize),
            "memory_flush_success": memory_flush_success
        }
    
    def truncate_to_fit(
        self,
        messages: List[Dict[str, Any]],
        max_tokens: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Truncate messages to fit within token limit.
        
        Keeps most recent messages, drops oldest first.
        This is a fallback when compaction hasn't run.
        """
        limit = max_tokens or (self._max_tokens - self._reserve_tokens)
        
        # Start from end, accumulate until we hit limit
        result = []
        tokens = 0
        
        for msg in reversed(messages):
            msg_tokens = self.count_message_tokens(msg)
            if tokens + msg_tokens > limit:
                break
            result.insert(0, msg)
            tokens += msg_tokens
        
        if len(result) < len(messages):
            logger.warning(
                f"Truncated {len(messages) - len(result)} messages "
                f"to fit context limit"
            )
        
        return result
    
    def split_messages_by_token_share(
        self,
        messages: List[Dict[str, Any]],
        parts: int = DEFAULT_PARTS
    ) -> List[List[Dict[str, Any]]]:
        """
        Split messages into chunks by token share.
        
        From Clawdbot's splitMessagesByTokenShare - splits large context
        into roughly equal token-sized chunks for multi-part summarization.
        
        Args:
            messages: Messages to split
            parts: Number of parts to split into
            
        Returns:
            List of message chunks
        """
        if not messages:
            return []
        
        # Normalize parts
        normalized_parts = max(1, min(parts, len(messages)))
        if normalized_parts <= 1:
            return [messages]
        
        total_tokens = self.count_messages_tokens(messages)
        target_tokens = total_tokens / normalized_parts
        
        chunks: List[List[Dict[str, Any]]] = []
        current: List[Dict[str, Any]] = []
        current_tokens = 0
        
        for message in messages:
            msg_tokens = self.count_message_tokens(message)
            
            # Check if we should start a new chunk
            if (len(chunks) < normalized_parts - 1 and
                len(current) > 0 and
                current_tokens + msg_tokens > target_tokens):
                chunks.append(current)
                current = []
                current_tokens = 0
            
            current.append(message)
            current_tokens += msg_tokens
        
        # Add remaining messages
        if current:
            chunks.append(current)
        
        return chunks
    
    def chunk_messages_by_max_tokens(
        self,
        messages: List[Dict[str, Any]],
        max_tokens: int = MAX_TOKENS_PER_CHUNK
    ) -> List[List[Dict[str, Any]]]:
        """
        Chunk messages ensuring no chunk exceeds max tokens.
        
        From Clawdbot's chunkMessagesByMaxTokens - ensures each chunk
        is within model limits for summarization.
        
        Args:
            messages: Messages to chunk
            max_tokens: Maximum tokens per chunk
            
        Returns:
            List of message chunks
        """
        if not messages:
            return []
        
        chunks: List[List[Dict[str, Any]]] = []
        current_chunk: List[Dict[str, Any]] = []
        current_tokens = 0
        
        for message in messages:
            msg_tokens = self.count_message_tokens(message)
            
            # Check if adding this message would exceed limit
            if current_chunk and current_tokens + msg_tokens > max_tokens:
                chunks.append(current_chunk)
                current_chunk = []
                current_tokens = 0
            
            current_chunk.append(message)
            current_tokens += msg_tokens
            
            # Handle oversized individual messages
            if msg_tokens > max_tokens:
                chunks.append(current_chunk)
                current_chunk = []
                current_tokens = 0
        
        # Add remaining messages
        if current_chunk:
            chunks.append(current_chunk)
        
        return chunks
    
    def compute_adaptive_chunk_ratio(
        self,
        messages: List[Dict[str, Any]]
    ) -> float:
        """
        Compute adaptive chunk ratio based on average message size.
        
        From Clawdbot - when messages are large, we use smaller chunks
        to avoid exceeding model limits during summarization.
        """
        if not messages:
            return BASE_CHUNK_RATIO
        
        total_tokens = self.count_messages_tokens(messages)
        avg_tokens_per_message = total_tokens / len(messages)
        
        # If average message is large, use smaller chunks
        if avg_tokens_per_message > 2000:
            return MIN_CHUNK_RATIO
        elif avg_tokens_per_message > 1000:
            return (BASE_CHUNK_RATIO + MIN_CHUNK_RATIO) / 2
        
        return BASE_CHUNK_RATIO


# Singleton instance
_context_manager: Optional[ContextManager] = None


def get_context_manager() -> ContextManager:
    """Get singleton ContextManager instance."""
    global _context_manager
    if _context_manager is None:
        _context_manager = ContextManager()
    return _context_manager
