"""
Agent Runner - Claude SDK Integration

The central orchestrator for agent conversations with:
1. Claude SDK client management
2. Tool execution with retry logic
3. Streaming responses
4. Error handling and failover
5. Integration with Session and Memory managers
6. Steering queue for mid-run interruption (pi-agent pattern)
7. Message transformation for cross-provider compatibility
8. Turn lifecycle events

Based on Clawdbot's agent loop patterns (pi-agent + embedded runner).
"""
import logging
import re
import random
from typing import List, Dict, Any, Optional, AsyncIterator, Callable, Awaitable
from dataclasses import dataclass, field
import asyncio
from datetime import datetime, timezone

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type
)

from core.config import settings
from core.session import Session, SessionManager
from core.context import get_context_manager
from core.stream_chunker import (
    create_stream_chunker,
    StreamBlockChunker,
    strip_block_tags,
    ChunkerState as StreamChunkerState
)
from core.message_transform import (
    transform_messages,
    TransformConfig,
    prune_tool_results_in_messages
)
from core.errors import (
    classify_error,
    ErrorType,
    CompactionFailedError,
    get_user_friendly_message,
    is_retryable
)
from core.lanes import session_lane, global_lane
from core.turn_validation import (
    validate_anthropic_turns,
    limit_history_turns,
    ensure_valid_turn_start
)
from core.failover import (
    FailoverError,
    FailoverReason,
    with_compaction_retry,
    CompactionRetryConfig
)
from core.cache_trace import create_cache_trace
from core.tool_call_id import sanitize_tool_call_ids_in_messages
from core.thought_signatures import sanitize_user_facing_text
from tools.policy import get_tool_policy

logger = logging.getLogger("brainmap.agent")

# Constants
SILENT_REPLY_TOKEN = "[SILENT]"

# Message deduplication constants from Clawdbot (messaging-dedupe.ts)
MIN_DUPLICATE_TEXT_LENGTH = 10
MAX_SENT_TEXTS = 200  # Circular buffer size

# Human-like reply delay constants from Clawdbot (reply-dispatcher.ts)
# DISABLED: For voice assistants, we want instant streaming. The frontend handles
# smooth animation via TypewriterMarkdown component (2 words every 80ms).
# Server-side delays just add latency without benefit.
# Clawdbot uses this for Slack/chat apps where typing simulation makes sense.
HUMAN_DELAY_MIN_MS = 0     # Disabled - was 800
HUMAN_DELAY_MAX_MS = 0     # Disabled - was 2500
HUMAN_DELAY_ENABLED = False  # Disabled for voice assistant UX


def get_human_delay() -> float:
    """
    Get random human-like delay in seconds (from Clawdbot).

    Returns a random delay between 0.8-2.5 seconds to simulate
    natural human typing/reading speed between message chunks.
    """
    delay_ms = random.randint(HUMAN_DELAY_MIN_MS, HUMAN_DELAY_MAX_MS)
    return delay_ms / 1000


async def apply_human_delay(is_first_chunk: bool = False) -> None:
    """
    Apply human-like delay between chunks if enabled.

    Args:
        is_first_chunk: Skip delay for first chunk (immediate response feels better)
    """
    if not HUMAN_DELAY_ENABLED or is_first_chunk:
        return
    await asyncio.sleep(get_human_delay())


class ContextOverflowError(Exception):
    """Raised when context window is exceeded."""
    pass


def normalize_text_for_comparison(text: str) -> str:
    """
    Normalize text for duplicate comparison (from Clawdbot).

    - Strips whitespace
    - Lowercases
    - Removes emoji
    - Collapses whitespace
    """
    if not text:
        return ""
    result = text.strip().lower()
    # Remove emoji (Emoji_Presentation and Extended_Pictographic)
    result = re.sub(r'[\U0001F300-\U0001F9FF]', '', result)
    # Collapse whitespace
    result = re.sub(r'\s+', ' ', result)
    return result.strip()


def is_duplicate_message(text: str, sent_texts: List[str]) -> bool:
    """
    Check if message is a duplicate (from Clawdbot).

    Uses bidirectional substring match to catch near-duplicates.
    """
    if not sent_texts:
        return False

    normalized = normalize_text_for_comparison(text)
    if not normalized or len(normalized) < MIN_DUPLICATE_TEXT_LENGTH:
        return False

    for sent in sent_texts:
        sent_norm = normalize_text_for_comparison(sent)
        if not sent_norm or len(sent_norm) < MIN_DUPLICATE_TEXT_LENGTH:
            continue
        # Bidirectional substring match
        if normalized in sent_norm or sent_norm in normalized:
            return True

    return False


class MessageDeduplicator:
    """
    Track sent messages for deduplication (from Clawdbot).

    Uses a circular buffer to track recently sent messages
    and prevent duplicate sends.
    """

    def __init__(self, max_size: int = MAX_SENT_TEXTS):
        self._sent: List[str] = []
        self._max_size = max_size

    def is_duplicate(self, text: str) -> bool:
        """Check if text is a duplicate of a recently sent message."""
        return is_duplicate_message(text, self._sent)

    def record(self, text: str) -> None:
        """
        Record sent message (only call on successful send).

        Uses circular buffer - removes oldest when at max size.
        """
        self._sent.append(text)
        # Circular buffer - remove oldest
        while len(self._sent) > self._max_size:
            self._sent.pop(0)

    def clear(self) -> None:
        """Clear all recorded messages."""
        self._sent.clear()


class MessagingToolTracker:
    """
    Track messaging tool calls with pending/committed pattern (from Clawdbot pi-agent).
    
    This prevents duplicate messages when a tool call is retried or if the same
    message text is sent via multiple tool calls.
    
    Pattern:
    1. Before tool exec: mark_pending(tool_call_id, message_text)
    2. After successful send: commit(tool_call_id)
    3. Check is_duplicate() before allowing send
    
    From pi-embedded-subscribe.handlers.tools.ts:
    - pendingMessagingTexts: Map<string, string>
    - commitMessagingToolCall(toolCallId)
    - isMessagingTextDuplicate(text)
    """
    
    def __init__(self, max_committed: int = MAX_SENT_TEXTS):
        self._pending: Dict[str, str] = {}  # tool_call_id -> text
        self._committed: List[str] = []  # committed texts (normalized)
        self._max_committed = max_committed
        
    def mark_pending(self, tool_call_id: str, text: str) -> None:
        """Mark a messaging tool call as pending (about to send)."""
        self._pending[tool_call_id] = text
        
    def commit(self, tool_call_id: str) -> None:
        """
        Commit a messaging tool call (send succeeded).
        
        Moves the text from pending to committed list for dedup checking.
        """
        text = self._pending.pop(tool_call_id, None)
        if text:
            normalized = normalize_text_for_comparison(text)
            if normalized and len(normalized) >= MIN_DUPLICATE_TEXT_LENGTH:
                self._committed.append(normalized)
                # Circular buffer
                while len(self._committed) > self._max_committed:
                    self._committed.pop(0)
    
    def rollback(self, tool_call_id: str) -> None:
        """Rollback a pending messaging tool call (send failed/aborted)."""
        self._pending.pop(tool_call_id, None)
    
    def is_duplicate(self, text: str) -> bool:
        """
        Check if text is a duplicate of a committed or pending message.
        
        Checks both committed messages and pending messages to catch
        duplicates even within the same turn.
        """
        normalized = normalize_text_for_comparison(text)
        if not normalized or len(normalized) < MIN_DUPLICATE_TEXT_LENGTH:
            return False
        
        # Check committed
        for committed_norm in self._committed:
            if normalized in committed_norm or committed_norm in normalized:
                return True
        
        # Check pending
        for pending_text in self._pending.values():
            pending_norm = normalize_text_for_comparison(pending_text)
            if pending_norm and (normalized in pending_norm or pending_norm in normalized):
                return True
        
        return False
    
    def clear(self) -> None:
        """Clear all tracking."""
        self._pending.clear()
        self._committed.clear()


def is_silent_reply(text: str) -> bool:
    """Check if the response is a silent reply token."""
    if not text:
        return False
    return text.strip() == SILENT_REPLY_TOKEN


@dataclass
class CodeSpan:
    """Represents a code span (inline or fenced) in text."""
    start: int
    end: int


def find_code_spans(text: str) -> List[CodeSpan]:
    """
    Find all inline code and fenced code blocks.

    Used to protect code examples containing thinking tags from being stripped.
    """
    spans = []

    # Fenced blocks (``` or ~~~)
    fence_re = re.compile(r'(```|~~~).*?\1', re.DOTALL)
    for m in fence_re.finditer(text):
        spans.append(CodeSpan(m.start(), m.end()))

    # Inline code (backticks)
    inline_re = re.compile(r'`[^`]+`')
    for m in inline_re.finditer(text):
        # Don't add if inside fenced block
        if not any(s.start <= m.start() < s.end for s in spans):
            spans.append(CodeSpan(m.start(), m.end()))

    return spans


def is_in_code_span(pos: int, spans: List[CodeSpan]) -> bool:
    """Check if position is inside any code span."""
    return any(s.start <= pos < s.end for s in spans)


# Thinking tag regex matching ALL variants from Clawdbot pi-embedded-subscribe.ts
THINKING_TAG_RE = re.compile(
    r'<\s*(/?)\s*(?:think(?:ing)?|thought|antthinking)\s*>',
    re.IGNORECASE
)


def filter_thinking_blocks(text: str) -> str:
    """
    Filter out thinking/reasoning blocks from response.

    Clawdbot pattern: Strip <think>...</think> tags and their content
    to prevent internal reasoning from leaking to the user.

    IMPORTANT: Does NOT strip thinking tags inside code spans (backticks or fences).
    This prevents breaking code examples that mention these tags.

    Handles:
    - <think>...</think>
    - <thinking>...</thinking>
    - <thought>...</thought>
    - <antThinking>...</antThinking>
    - <final>...</final> tags (extract content only)
    - Unclosed tags (safe behavior - keep content before tag)
    """
    if not text:
        return text

    # Check if there's a <final> block - if so, extract only that
    # DON'T strip - preserve whitespace for proper word separation in streaming
    final_match = re.search(r'<final>(.*?)</final>', text, re.DOTALL)
    if final_match:
        return final_match.group(1)

    # Remove <final> tags but keep content (no closing tag case)
    text = re.sub(r'</?final>', '', text)

    # Find code spans to protect
    code_spans = find_code_spans(text)

    result = []
    last_end = 0
    in_thinking = False

    for match in THINKING_TAG_RE.finditer(text):
        # Skip if inside code span (protect code examples)
        if is_in_code_span(match.start(), code_spans):
            continue

        is_closing = match.group(1) == '/'

        if not in_thinking and not is_closing:
            # Opening tag - keep text before it
            result.append(text[last_end:match.start()])
            in_thinking = True
        elif in_thinking and is_closing:
            # Closing tag - skip content inside thinking block
            in_thinking = False

        last_end = match.end()

    # Append remaining text (only if not in unclosed thinking block)
    if not in_thinking:
        result.append(text[last_end:])

    # Also handle <reasoning> blocks (not code-span aware for simplicity)
    filtered = ''.join(result)
    filtered = re.sub(r'<reasoning>.*?</reasoning>', '', filtered, flags=re.DOTALL)

    # DON'T strip - preserve leading/trailing whitespace for markdown formatting
    # Newlines are critical for paragraphs, headers, lists
    return filtered


def process_response(text: str) -> tuple[str, bool]:
    """
    Process agent response: filter thinking blocks and detect silent replies.
    
    Returns:
        Tuple of (processed_text, is_silent)
    """
    # First filter thinking/reasoning blocks
    filtered = filter_thinking_blocks(text)
    
    # Check for silent reply
    if is_silent_reply(filtered):
        return "", True
    
    return filtered, False


@dataclass
class AgentResponse:
    """Response from an agent run."""
    content: str
    tool_calls: List[Dict[str, Any]]
    usage: Dict[str, int]
    stop_reason: str
    error: Optional[str] = None


@dataclass
class StreamChunk:
    """A chunk of streaming response."""
    type: str  # "text", "tool_start", "tool_end", "error", "done"
    content: str
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class SteeringMessage:
    """A steering message to interrupt the agent mid-run."""
    content: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class AgentRunner:
    """
    Runs agent conversations with Claude.
    
    This is the core agent loop that:
    1. Builds context (system prompt + memory + session)
    2. Sends to Claude with tools
    3. Handles tool execution
    4. Streams responses
    5. Manages errors with retry/failover
    6. Supports steering (mid-run interruption)
    7. Supports follow-up messages
    8. Emits turn lifecycle events
    
    Based on pi-agent's Agent class + Clawdbot's embedded runner.
    """
    
    def __init__(
        self,
        session_manager: SessionManager,
        tool_registry: Optional[Any] = None,
        prompt_builder: Optional[Any] = None,
        transform_context: Optional[Callable[[List[Dict]], Awaitable[List[Dict]]]] = None
    ):
        """
        Initialize agent runner.

        Args:
            session_manager: For session state and compaction
            tool_registry: Tool definitions and execution
            prompt_builder: System prompt construction
            transform_context: Optional hook to transform messages before LLM call
        """
        self._session = session_manager
        self._tools = tool_registry
        self._prompt = prompt_builder
        self._context = get_context_manager()
        self._client = None
        
        # Message transformation (pi-agent pattern)
        self._transform_context = transform_context
        
        # Steering queue (pi-agent pattern)
        # Messages added here interrupt the current run after tool execution
        self._steering_queue: List[SteeringMessage] = []
        
        # Follow-up queue (pi-agent pattern)
        # Messages added here are processed after the agent would normally stop
        self._follow_up_queue: List[SteeringMessage] = []
        
        # Steering mode: "all" = send all at once, "one-at-a-time" = one per turn
        self._steering_mode: str = "one-at-a-time"
        self._follow_up_mode: str = "one-at-a-time"
        
        # Message deduplication (from Clawdbot)
        self._deduplicator = MessageDeduplicator()
        
        # Messaging tool dedup with pending/committed tracking (pi-agent pattern)
        self._messaging_tracker = MessagingToolTracker()
        
        # Agent lifecycle hooks (from Clawdbot)
        self._pre_hooks: List[Callable] = []
        self._post_hooks: List[Callable] = []
        
        # Running state
        self._is_streaming = False
        self._abort_controller: Optional[asyncio.Event] = None

    def steer(self, message: str) -> None:
        """
        Queue a steering message to interrupt the agent mid-run.
        
        From pi-agent: Delivered after current tool execution, skips remaining tools.
        """
        self._steering_queue.append(SteeringMessage(content=message))
        logger.debug(f"Steering message queued: {message[:50]}...")

    def follow_up(self, message: str) -> None:
        """
        Queue a follow-up message to be processed after agent finishes.
        
        From pi-agent: Delivered only when agent has no more tool calls or steering.
        """
        self._follow_up_queue.append(SteeringMessage(content=message))
        logger.debug(f"Follow-up message queued: {message[:50]}...")

    def clear_steering_queue(self) -> None:
        """Clear all steering messages."""
        self._steering_queue.clear()

    def clear_follow_up_queue(self) -> None:
        """Clear all follow-up messages."""
        self._follow_up_queue.clear()

    def clear_all_queues(self) -> None:
        """Clear both steering and follow-up queues."""
        self._steering_queue.clear()
        self._follow_up_queue.clear()

    def abort(self) -> None:
        """Abort the current run."""
        if self._abort_controller:
            self._abort_controller.set()

    async def _get_steering_messages(self) -> List[str]:
        """Get pending steering messages (pi-agent pattern)."""
        if not self._steering_queue:
            return []
        
        if self._steering_mode == "one-at-a-time":
            msg = self._steering_queue.pop(0)
            return [msg.content]
        else:
            messages = [m.content for m in self._steering_queue]
            self._steering_queue.clear()
            return messages

    async def _get_follow_up_messages(self) -> List[str]:
        """Get pending follow-up messages (pi-agent pattern)."""
        if not self._follow_up_queue:
            return []
        
        if self._follow_up_mode == "one-at-a-time":
            msg = self._follow_up_queue.pop(0)
            return [msg.content]
        else:
            messages = [m.content for m in self._follow_up_queue]
            self._follow_up_queue.clear()
            return messages

    @property
    def is_streaming(self) -> bool:
        """Check if agent is currently streaming."""
        return self._is_streaming

    def add_pre_hook(self, hook: Callable) -> None:
        """Register a pre-turn hook (may modify user_message)."""
        self._pre_hooks.append(hook)

    def add_post_hook(self, hook: Callable) -> None:
        """Register a post-turn hook."""
        self._post_hooks.append(hook)

    async def _run_pre_hooks(self, session: Session, user_message: str) -> str:
        """Run pre-turn hooks, allowing message modification."""
        msg = user_message
        for hook in self._pre_hooks:
            try:
                if asyncio.iscoroutinefunction(hook):
                    result = await hook(session=session, user_message=msg)
                else:
                    result = hook(session=session, user_message=msg)
                if isinstance(result, str) and result.strip():
                    msg = result
            except Exception as e:
                logger.warning(f"Pre-hook failed: {e}")
        return msg

    async def _run_post_hooks(
        self,
        session: Session,
        user_message: str,
        assistant_response: str
    ) -> None:
        """Run post-turn hooks."""
        for hook in self._post_hooks:
            try:
                if asyncio.iscoroutinefunction(hook):
                    await hook(session=session, user_message=user_message, assistant_response=assistant_response)
                else:
                    hook(session=session, user_message=user_message, assistant_response=assistant_response)
            except Exception as e:
                logger.warning(f"Post-hook failed: {e}")

    def _resolve_tool_definitions(self, session: Session) -> Optional[List[Dict[str, Any]]]:
        """Resolve tool definitions filtered by policy for this user."""
        if not self._tools:
            return None
        policy = get_tool_policy()
        allowlist = set(policy.resolve(session.user_id))
        return self._tools.get_tool_definitions(allowlist=allowlist)

    def _should_prefetch_memory(self, user_message: str) -> bool:
        """Heuristic check for past-info queries that should prefetch memory."""
        if not settings.MEMORY_PREFETCH_ENABLED:
            return False

        text = user_message.strip().lower()
        if len(text) < settings.MEMORY_PREFETCH_MIN_CHARS:
            return False
        if len(text) > settings.MEMORY_PREFETCH_MAX_CHARS:
            return True
        if "memory_search" in text or "search notes" in text:
            return False
        if text in {"hi", "hey", "hello", "yo", "sup", "wassup", "what's up"}:
            return False

        patterns = [
            r"\bremember\b",
            r"\bremind me\b",
            r"\blast time\b",
            r"\bearlier\b",
            r"\bprevious\b",
            r"\bwhat did we\b",
            r"\bwhat were we\b",
            r"\bwhat have we\b",
            r"\bwe discussed\b",
            r"\byou said\b",
            r"\bmy (?:favorite|preference|preferences)\b",
            r"\bdo i have\b",
            r"\bmy notes?\b",
            r"\bnotes? about\b",
            r"\btasks?\b",
            r"\btodos?\b",
            r"\breminders?\b",
            r"\bmeeting\b",
            r"\bappointment\b",
            r"\bschedule\b",
            r"\bcalendar\b",
        ]
        for pattern in patterns:
            if re.search(pattern, text):
                return True
        return "?" in text and any(
            kw in text for kw in ("past", "previous", "earlier", "last", "before")
        )

    def _resolve_prompt_mode(self, user_message: str) -> str:
        """Resolve prompt mode for this turn (full/minimal/none)."""
        if settings.PROMPT_MINIMAL_ON_SHORT:
            max_chars = max(1, int(settings.PROMPT_MINIMAL_MAX_CHARS))
            if len(user_message.strip()) <= max_chars:
                return "minimal"
        return "full"

    def _truncate_tool_output(self, value: str) -> str:
        """Truncate tool output for streaming payloads."""
        max_chars = max(0, int(settings.STREAM_TOOL_OUTPUT_MAX_CHARS))
        if max_chars <= 0 or len(value) <= max_chars:
            return value
        return value[:max_chars] + "..."

    def _prune_tool_results_in_messages(
        self,
        messages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Prune oversized tool_result blocks using soft-trim + hard-clear.
        
        Uses the new message_transform.prune_tool_results_in_messages
        which implements Clawdbot's two-phase pruning:
        1. Soft-trim: Keep head + tail of large results
        2. Hard-clear: Replace very large results with placeholder
        """
        # Use soft-trim threshold (4000 chars) and hard-clear threshold
        hard_clear_min = max(0, int(settings.CONTEXT_PRUNE_HARD_CLEAR_MIN_CHARS))
        placeholder = settings.CONTEXT_PRUNE_HARD_CLEAR_PLACEHOLDER
        
        if hard_clear_min <= 0:
            return messages
        
        # Soft-trim at 4000 chars, hard-clear at the configured threshold
        return prune_tool_results_in_messages(
            messages,
            max_chars=4000,  # Soft-trim threshold
            head_chars=1500,
            tail_chars=500,
            hard_clear_threshold=hard_clear_min,
            hard_clear_placeholder=placeholder
        )
    async def _prefetch_memory(self, user_message: str) -> Optional[str]:
        """Prefetch memory_search results and format for prompt injection."""
        if not self._should_prefetch_memory(user_message):
            return None
        try:
            from tools.memory_tools import memory_search
            result = await memory_search(
                query=user_message,
                max_results=5,
                min_score=0.3
            )
        except Exception as e:
            logger.warning(f"Auto memory_search failed: {e}")
            return None

        if not isinstance(result, dict) or not result.get("success"):
            return None
        results = result.get("results") or []
        if not results:
            return None

        lines = ["Memory search results (auto):", f"Query: {user_message}", "Results:"]
        for idx, item in enumerate(results[:5], start=1):
            source = item.get("source", "unknown")
            title = item.get("title") or item.get("path") or ""
            content = item.get("content") or item.get("text") or ""
            snippet = " ".join(content.split())
            if len(snippet) > 200:
                snippet = snippet[:200] + "..."
            if title:
                lines.append(f"{idx}. [{source}] {title} - {snippet}")
            else:
                lines.append(f"{idx}. [{source}] {snippet}")

        return "\n".join(lines)

    async def _auto_capture_memory(
        self,
        session: Session,
        user_message: str,
        assistant_response: str
    ) -> None:
        """
        Auto-capture memories after each exchange (from Clawdbot lifecycle hooks).

        This implements the "agent_end" hook pattern from Clawdbot that
        automatically extracts and saves important information from conversations.
        """
        # Get memory manager from session manager
        memory_manager = getattr(self._session, '_memory_manager', None)
        if not memory_manager:
            return

        try:
            result = await memory_manager.auto_capture_from_exchange(
                user_message=user_message,
                assistant_response=assistant_response,
                session_id=session.id
            )
            if result.get("captured"):
                logger.debug(
                    f"Auto-captured memory: {result.get('facts_saved', 0)} facts, "
                    f"daily_logged={result.get('daily_logged', False)}"
                )
        except Exception as e:
            # Don't fail the response for memory capture errors
            logger.warning(f"Auto memory capture failed: {e}")

    async def _get_client(self):
        """Get or create Claude client."""
        if self._client is None:
            try:
                import anthropic
                # Enable prompt caching beta feature
                self._client = anthropic.AsyncAnthropic(
                    api_key=settings.ANTHROPIC_API_KEY,
                    default_headers={
                        "anthropic-beta": "prompt-caching-2024-07-31"
                    }
                )
            except ImportError:
                raise RuntimeError(
                    "anthropic package not installed. "
                    "Run: pip install anthropic"
                )
        return self._client
    
    async def run(
        self,
        session: Session,
        user_message: str,
        system_prompt: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None
    ) -> AgentResponse:
        """
        Run a single agent turn (non-streaming).
        
        Args:
            session: Current conversation session
            user_message: User's input
            system_prompt: Override system prompt
            tools: Tool definitions (uses registry if not provided)
        
        Returns:
            AgentResponse with content and tool calls
        """
        # Use session lane to prevent concurrent requests to same session
        async with session_lane(session.id, f"run:{user_message[:20]}"):
            # Pre-turn hooks
            user_message = await self._run_pre_hooks(session, user_message)

            # Add user message to session
            await self._session.add_message(session, "user", user_message)

            session.metadata.setdefault("lifecycle_events", []).append({
                "phase": "start",
                "timestamp": datetime.now(timezone.utc).isoformat()
            })

            # Clawdbot-style: do not auto-prefetch memory into the prompt
        
            # Get messages for API
            raw_messages = session.get_api_messages(prune=settings.CONTEXT_PRUNE_ENABLED)
        
            # Check context and truncate if needed
            if self._context.needs_compaction(raw_messages):
                logger.warning("Context needs compaction - truncating for now")
                raw_messages = self._context.truncate_to_fit(raw_messages)

            # Soft-trim oversized message blocks (Clawdbot-like pruning)
            messages = self._context.soft_trim_messages(raw_messages)
        
            # Build system prompt if not provided
            prompt_report = None
            if system_prompt is None and self._prompt:
                prompt_mode = self._resolve_prompt_mode(user_message)
                if hasattr(self._prompt, "build_with_report"):
                    report = await self._prompt.build_with_report(
                        session.user_id,
                        context={"last_message": user_message, "skip_memory": True},
                        mode=prompt_mode
                    )
                    system_prompt = report.get("prompt", "")
                    prompt_report = report
                else:
                    system_prompt = await self._prompt.build(
                        session.user_id,
                        context={"last_message": user_message, "skip_memory": True},
                        mode=prompt_mode
                    )
            system_prompt = system_prompt or self._default_system_prompt()
        
            # Get tools if not provided
            if tools is None and self._tools:
                tools = self._resolve_tool_definitions(session)
        
            # Make API call with retry
            response = await self._call_claude_with_retry(
                messages=messages,
                system=system_prompt,
                tools=tools
            )
        
            # Add assistant response to session
            if response.content:
                await self._session.add_message(
                    session, "assistant", response.content
                )

            if prompt_report:
                session.metadata["last_prompt_report"] = prompt_report

            if response.usage:
                session.metadata["last_cache_stats"] = {
                    "input_tokens": response.usage.get("input_tokens"),
                    "output_tokens": response.usage.get("output_tokens"),
                    "cache_read_tokens": response.usage.get("cache_read_tokens"),
                    "cache_write_tokens": response.usage.get("cache_write_tokens")
                }

            await self._run_post_hooks(session, user_message, response.content or "")

            session.metadata.setdefault("lifecycle_events", []).append({
                "phase": "end",
                "timestamp": datetime.now(timezone.utc).isoformat()
            })
        
            return response
    
    async def run_stream(
        self,
        session: Session,
        user_message: str,
        system_prompt: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_handler: Optional[Callable] = None
    ) -> AsyncIterator[StreamChunk]:
        """
        Run agent turn with streaming responses and proper agentic loop.

        Implements the full Anthropic tool use pattern:
        1. Send message to Claude
        2. If Claude calls a tool, execute it and send result back
        3. Repeat until Claude responds with text (no more tool calls)

        This ensures Claude can always generate a text response after using tools.

        Uses lane queueing from Clawdbot to prevent race conditions:
        - Session lane: prevents concurrent ops on same session
        - Global lane: used during compaction to prevent conflicts
        """
        # Use session lane to prevent concurrent requests to same session
        # This is CRITICAL from Clawdbot - prevents state corruption
        async with session_lane(session.id, f"run_stream:{user_message[:20]}"):
            async for chunk in self._run_stream_internal(
                session, user_message, system_prompt, tools, tool_handler
            ):
                yield chunk

    async def _run_stream_internal(
        self,
        session: Session,
        user_message: str,
        system_prompt: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_handler: Optional[Callable] = None
    ) -> AsyncIterator[StreamChunk]:
        """Internal stream implementation with lane protection.

        Note: This is called from within session_lane(), so we already hold
        the session lock. DO NOT acquire additional session locks here to
        avoid deadlock (asyncio.Lock is not reentrant).
        """
        # Pre-turn hooks
        user_message = await self._run_pre_hooks(session, user_message)

        # Add user message (auto_compact=False, we'll handle compaction manually for streaming)
        # Note: No nested lock needed - we're already inside session_lane
        await self._session.add_message(session, "user", user_message, auto_compact=False)

        session.metadata.setdefault("lifecycle_events", []).append({
            "phase": "start",
            "timestamp": datetime.now(timezone.utc).isoformat()
        })

        yield StreamChunk(
            type="lifecycle",
            content="start",
            metadata={"phase": "start"}
        )

        # Clawdbot-style: do not auto-prefetch memory into the prompt

        # Get messages for API
        raw_messages = session.get_api_messages(prune=settings.CONTEXT_PRUNE_ENABLED)

        # Check if compaction is needed - trigger FULL compaction, not just truncation
        # This is critical: streaming mode must also flush to memory and summarize
        # Note: We already hold session lock, only acquire global lane for compaction
        if self._context.needs_compaction(raw_messages):
            logger.info("Context needs compaction before stream - triggering full compaction")
            try:
                # Only acquire global lane (we already have session lock)
                async with global_lane("compaction"):
                    yield StreamChunk(
                        type="lifecycle",
                        content="compaction_start",
                        metadata={"phase": "compaction_start"}
                    )
                    # Trigger the proper compaction flow (flush to memory + summarize)
                    await self._session._compact_session(session)
                # Refresh messages after compaction
                raw_messages = session.get_api_messages()
                logger.info(f"Compaction complete, now have {len(raw_messages)} messages")
                yield StreamChunk(
                    type="lifecycle",
                    content="compaction_end",
                    metadata={"phase": "compaction_end"}
                )
            except Exception as e:
                logger.error(f"Compaction failed, falling back to truncation: {e}")
                raw_messages = self._context.truncate_to_fit(raw_messages)

        # Soft-trim oversized message blocks (Clawdbot-like pruning)
        messages = self._context.soft_trim_messages(raw_messages)
        messages = self._prune_tool_results_in_messages(messages)
        
        # Message transformation for cross-provider compatibility (pi-agent pattern)
        # Handles: orphaned tool calls, ID normalization, errored message removal
        messages = transform_messages(messages, TransformConfig())
        
        # Validate turn ordering (Clawdbot pattern)
        messages = validate_anthropic_turns(messages)
        messages = ensure_valid_turn_start(messages)
        
        # Limit history to prevent context bloat
        messages = limit_history_turns(messages, max_turns=50)
        
        # Sanitize tool call IDs for provider compatibility
        messages = sanitize_tool_call_ids_in_messages(messages)
        
        # Apply optional transformContext hook (pi-agent pattern)
        if self._transform_context:
            try:
                messages = await self._transform_context(messages)
            except Exception as e:
                logger.warning(f"transformContext hook failed: {e}")
        
        # Create cache trace for diagnostics
        cache_trace = create_cache_trace(
            session_id=session.id,
            provider="anthropic",
            model_id=settings.CLAUDE_MODEL
        )
        cache_trace.record_stage("prompt:before", messages=messages, system=system_prompt)
        
        # Build system prompt with context for memory retrieval
        prompt_report = None
        if system_prompt is None and self._prompt:
            prompt_mode = self._resolve_prompt_mode(user_message)
            # Pass the user message for memory context retrieval
            prompt_context = {
                "last_message": user_message,
                "channel": "stream",
                "skip_memory": True
            }
            if hasattr(self._prompt, "build_with_report"):
                report = await self._prompt.build_with_report(
                    session.user_id,
                    context=prompt_context,
                    mode=prompt_mode
                )
                system_prompt = report.get("prompt", "")
                prompt_report = report
            else:
                system_prompt = await self._prompt.build(
                    session.user_id,
                    context=prompt_context,
                    mode=prompt_mode
                )
        system_prompt = system_prompt or self._default_system_prompt()
        
        # Get tools
        if tools is None and self._tools:
            tools = self._resolve_tool_definitions(session)
        
        full_response = []
        
        try:
            client = await self._get_client()
            
            # Use cached system prompt format
            cached_system = [
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"}
                }
            ]
            
            # Agentic loop - continue until no more tool calls
            max_iterations = 10  # Prevent infinite loops
            iteration = 0
            
            # Create chunker for streaming
            # For voice assistants: Use small buffer for low latency
            # Frontend handles smooth animation via TypewriterMarkdown
            # (ChatGPT/Claude/Perplexity all stream fast and animate client-side)
            chunker = create_stream_chunker(
                min_chars=20,      # Reasonable buffer for markdown structures
                max_chars=500,     # Large enough to preserve markdown lists/paragraphs
                break_preference="paragraph"  # Preserve markdown boundaries (lists, paragraphs)
            )
            
            # Clawdbot pattern: track accumulated text for monotonic guarantee
            last_streamed_norm: Optional[str] = None
            accumulated_text = ""  # Full accumulated response text
            last_accumulated_sent = ""  # Last accumulated sent (for monotonic check)
            message_started = False  # Track if we've emitted message_start
            
            # State for real-time thinking block stripping (Clawdbot pattern)
            stream_chunker_state = StreamChunkerState()
            
            self._is_streaming = True
            self._abort_controller = asyncio.Event()
            
            while iteration < max_iterations:
                iteration += 1
                
                # Check for abort
                if self._abort_controller.is_set():
                    logger.info("Agent run aborted by abort controller")
                    yield StreamChunk(type="lifecycle", content="aborted", metadata={"phase": "aborted"})
                    break
                
                # Emit turn_start event (pi-agent pattern)
                yield StreamChunk(
                    type="lifecycle",
                    content="turn_start",
                    metadata={"phase": "turn_start", "turn": iteration}
                )
                
                messages = self._prune_tool_results_in_messages(messages)
                tool_calls = []
                current_response = []
                stop_reason = None
                chunker.reset()  # Reset chunker for each iteration
                
                # Add cache_control to messages
                cached_messages = self._add_cache_control_to_messages(messages)
                
                async with client.messages.stream(
                    model=settings.CLAUDE_MODEL,
                    max_tokens=settings.RESERVE_RESPONSE_TOKENS,
                    system=cached_system,
                    messages=cached_messages,
                    tools=tools or []
                ) as stream:
                    async for event in stream:
                        # Log cache usage
                        if event.type == "message_start":
                            usage = getattr(event.message, 'usage', None)
                            if usage:
                                cache_read = getattr(usage, 'cache_read_input_tokens', 0) or 0
                                cache_write = getattr(usage, 'cache_creation_input_tokens', 0) or 0
                                input_tokens = getattr(usage, 'input_tokens', 0) or 0
                                logger.info(f"📊 Token usage - Input: {input_tokens}, Cache read: {cache_read}, Cache write: {cache_write}")
                                if cache_read > 0:
                                    logger.info(f"✅ CACHE HIT! {cache_read} tokens from cache")
                                elif cache_write > 0:
                                    logger.info(f"📝 Cache created: {cache_write} tokens")
                                session.metadata["last_cache_stats"] = {
                                    "input_tokens": input_tokens,
                                    "cache_read_tokens": cache_read,
                                    "cache_write_tokens": cache_write
                                }
                        
                        elif event.type == "content_block_delta":
                            delta_type = getattr(event.delta, 'type', 'unknown')

                            if delta_type == "text_delta" or hasattr(event.delta, "text"):
                                text = event.delta.text
                                current_response.append(text)
                                full_response.append(text)
                                
                                # Clawdbot pattern: Emit message_start before first text
                                if not message_started:
                                    message_started = True
                                    yield StreamChunk(
                                        type="lifecycle",
                                        content="message_start",
                                        metadata={"phase": "message_start", "role": "assistant"}
                                    )

                                # Use chunker for smoother streaming with human-like delays
                                if text:
                                    for chunk in chunker.process(text):
                                        # Apply human delay between chunks (not first)
                                        await apply_human_delay(is_first_chunk=(len(full_response) == 1))
                                        
                                        # Real-time thinking block strip (Clawdbot pattern)
                                        cleaned_chunk = strip_block_tags(chunk, stream_chunker_state)
                                        if not cleaned_chunk or not cleaned_chunk.strip():
                                            continue  # Skip empty chunks after stripping
                                        
                                        # Track accumulated text for monotonic guarantee
                                        accumulated_text += cleaned_chunk
                                        
                                        # Monotonic text guarantee (Clawdbot pattern)
                                        # Ensure accumulated only grows, never shrinks
                                        if last_accumulated_sent and not accumulated_text.startswith(last_accumulated_sent):
                                            logger.warning("Non-monotonic stream detected, skipping chunk")
                                            continue
                                        
                                        if settings.STREAM_DEDUPLICATE_CHUNKS:
                                            norm = normalize_text_for_comparison(cleaned_chunk)
                                            if norm and norm == last_streamed_norm:
                                                continue
                                            last_streamed_norm = norm or last_streamed_norm
                                        
                                        # Emit with both delta and accumulated (Clawdbot pattern)
                                        last_accumulated_sent = accumulated_text
                                        yield StreamChunk(
                                            type="text",
                                            content=cleaned_chunk,
                                            metadata={
                                                "delta": cleaned_chunk,
                                                "accumulated": accumulated_text
                                            }
                                        )
                            
                            elif delta_type == "input_json_delta":
                                # Tool input being built - accumulate JSON
                                if tool_calls:
                                    partial = getattr(event.delta, 'partial_json', '')
                                    if 'partial_input' not in tool_calls[-1]:
                                        tool_calls[-1]['partial_input'] = ''
                                    tool_calls[-1]['partial_input'] += partial
                        
                        elif event.type == "content_block_start":
                            if event.content_block.type == "tool_use":
                                tool_name = event.content_block.name
                                logger.info(f"🔧 Tool call starting: {tool_name}")
                                yield StreamChunk(
                                    type="tool_start",
                                    content=tool_name,
                                    metadata={"id": event.content_block.id}
                                )
                                tool_calls.append({
                                    "id": event.content_block.id,
                                    "name": tool_name,
                                    "input": {}
                                })
                        
                        elif event.type == "content_block_stop":
                            # Parse accumulated JSON for tool input
                            if tool_calls and 'partial_input' in tool_calls[-1]:
                                import json
                                try:
                                    tool_calls[-1]['input'] = json.loads(tool_calls[-1]['partial_input'])
                                except (json.JSONDecodeError, ValueError, TypeError):
                                    tool_calls[-1]['input'] = {}
                                del tool_calls[-1]['partial_input']
                        
                        elif event.type == "message_delta":
                            stop_reason = getattr(event.delta, 'stop_reason', None)
                
                # Check if we need to continue (tool use) or stop (end_turn)
                if not tool_calls or stop_reason != "tool_use":
                    # Flush any remaining buffered content with human delay
                    for chunk in chunker.flush():
                        await apply_human_delay(is_first_chunk=False)
                        
                        # Real-time thinking block strip (Clawdbot pattern)
                        cleaned_chunk = strip_block_tags(chunk, stream_chunker_state)
                        if not cleaned_chunk or not cleaned_chunk.strip():
                            continue
                        
                        accumulated_text += cleaned_chunk
                        
                        if settings.STREAM_DEDUPLICATE_CHUNKS:
                            norm = normalize_text_for_comparison(cleaned_chunk)
                            if norm and norm == last_streamed_norm:
                                continue
                            last_streamed_norm = norm or last_streamed_norm
                        
                        last_accumulated_sent = accumulated_text
                        yield StreamChunk(
                            type="text",
                            content=cleaned_chunk,
                            metadata={
                                "delta": cleaned_chunk,
                                "accumulated": accumulated_text
                            }
                        )
                    
                    # Emit turn_end event (pi-agent pattern)
                    yield StreamChunk(
                        type="lifecycle",
                        content="turn_end",
                        metadata={"phase": "turn_end", "turn": iteration, "has_tool_calls": len(tool_calls) > 0}
                    )
                    
                    # Check for follow-up messages (pi-agent pattern)
                    follow_ups = await self._get_follow_up_messages()
                    if follow_ups:
                        for fu_msg in follow_ups:
                            logger.info(f"Processing follow-up message: {fu_msg[:50]}...")
                            messages.append({"role": "user", "content": fu_msg})
                        continue  # Keep the loop going
                    
                    # No tools called or Claude finished - we're done!
                    break
                
                # Execute tools and prepare tool results for next iteration
                tool_results = []
                for tool in tool_calls:
                    logger.info(f"🔧 Executing tool: {tool['name']}")

                    # Execute tool
                    tool_context = {"user_id": session.user_id, "session_id": session.id}
                    if tool_handler:
                        result = await tool_handler(tool["name"], tool["input"])
                    elif self._tools:
                        result_dict = await self._tools.execute(
                            tool["name"],
                            tool["input"],
                            context=tool_context,
                        )
                        if result_dict.get("status") == "success":
                            result = result_dict.get("result", "Done")
                        else:
                            result = result_dict.get("error", "Tool execution failed")
                    else:
                        result = f"Tool {tool['name']} not available"

                    # Convert result to string
                    if isinstance(result, dict):
                        import json
                        result_str = json.dumps(result)
                    else:
                        result_str = str(result)

                    logger.info(f"🔧 Tool {tool['name']} result: {result_str[:100]}...")

                    # Issue 3 Fix: Build action metadata in Fable-expected format
                    # Fable expects: {id, type, title, entity_id, preview}
                    action_metadata = self._build_action_metadata(tool["name"], result)

                    stream_output = ""
                    if settings.STREAM_TOOL_OUTPUT_ENABLED:
                        stream_output = self._truncate_tool_output(result_str)
                    yield StreamChunk(
                        type="tool_end",
                        content=stream_output,
                        metadata=action_metadata
                    )
                    
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool["id"],
                        "content": result_str
                    })
                
                # Add assistant message with tool use to history
                assistant_content = []
                for text in current_response:
                    if text:
                        assistant_content.append({"type": "text", "text": text})
                for tool in tool_calls:
                    assistant_content.append({
                        "type": "tool_use",
                        "id": tool["id"],
                        "name": tool["name"],
                        "input": tool["input"]
                    })
                
                messages.append({"role": "assistant", "content": assistant_content})
                
                # Add tool results as user message
                messages.append({"role": "user", "content": tool_results})
                
                # Emit turn_end event after tool execution (pi-agent pattern)
                yield StreamChunk(
                    type="lifecycle",
                    content="turn_end",
                    metadata={"phase": "turn_end", "turn": iteration, "has_tool_calls": True}
                )
                
                # Check for steering messages (pi-agent pattern)
                # Steering messages interrupt after tool execution
                steering_msgs = await self._get_steering_messages()
                if steering_msgs:
                    logger.info(f"Steering message received, adding to context")
                    for steer_msg in steering_msgs:
                        messages.append({"role": "user", "content": steer_msg})
                
                logger.info(f"🔄 Continuing agentic loop (iteration {iteration})")
            
            # Save final assistant response
            # Use sanitize_user_facing_text for comprehensive cleanup (Clawdbot pattern)
            response_text = sanitize_user_facing_text("".join(full_response))
            if response_text:
                # Check for duplicate message before saving/sending
                if self._deduplicator.is_duplicate(response_text):
                    logger.warning(f"Duplicate response detected, skipping: {response_text[:50]}...")
                else:
                    await self._session.add_message(session, "assistant", response_text)
                    # Record for future deduplication
                    self._deduplicator.record(response_text)
            
            # Record cache trace after completion
            cache_trace.record_stage("session:after", messages=messages)

            # Final flush of any remaining buffered content with human delay
            for chunk in chunker.flush():
                await apply_human_delay(is_first_chunk=False)
                
                # Real-time thinking block strip (Clawdbot pattern)
                cleaned_chunk = strip_block_tags(chunk, stream_chunker_state)
                if not cleaned_chunk or not cleaned_chunk.strip():
                    continue
                
                accumulated_text += cleaned_chunk
                
                if settings.STREAM_DEDUPLICATE_CHUNKS:
                    norm = normalize_text_for_comparison(cleaned_chunk)
                    if norm and norm == last_streamed_norm:
                        continue
                    last_streamed_norm = norm or last_streamed_norm
                
                last_accumulated_sent = accumulated_text
                yield StreamChunk(
                    type="text",
                    content=cleaned_chunk,
                    metadata={
                        "delta": cleaned_chunk,
                        "accumulated": accumulated_text
                    }
                )
            
            # Clawdbot pattern: Emit message_end with final cleaned text
            # This is the "source of truth" for what the AI actually said
            if message_started:
                yield StreamChunk(
                    type="lifecycle",
                    content="message_end",
                    metadata={
                        "phase": "message_end",
                        "role": "assistant",
                        "text": response_text,  # Final sanitized text
                        "accumulated": accumulated_text  # Raw accumulated
                    }
                )

            # Auto memory capture (from Clawdbot lifecycle hooks)
            # Capture important memories after each exchange
            await self._auto_capture_memory(
                session=session,
                user_message=user_message,
                assistant_response=response_text
            )

            if prompt_report:
                session.metadata["last_prompt_report"] = prompt_report

            await self._run_post_hooks(session, user_message, response_text)

            session.metadata.setdefault("lifecycle_events", []).append({
                "phase": "end",
                "timestamp": datetime.now(timezone.utc).isoformat()
            })

            yield StreamChunk(
                type="lifecycle",
                content="end",
                metadata={"phase": "end"}
            )

            yield StreamChunk(type="done", content="")
            
        except Exception as e:
            logger.error(f"Stream error: {e}")
            yield StreamChunk(type="error", content=str(e))
        
        finally:
            # Clean up streaming state
            self._is_streaming = False
            self._abort_controller = None
    
    def _add_cache_control_to_messages(
        self,
        messages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Add cache_control to last user message (like pi-agent).
        
        This caches conversation history, making subsequent requests
        much cheaper and not counting toward rate limits.
        """
        if not messages:
            return messages
        
        # Deep copy to avoid mutating original
        import copy
        cached = copy.deepcopy(messages)
        
        # Find last user message and add cache_control
        for i in range(len(cached) - 1, -1, -1):
            msg = cached[i]
            if msg.get("role") == "user":
                content = msg.get("content")
                
                if isinstance(content, str):
                    # Convert string content to block format with cache_control
                    msg["content"] = [
                        {
                            "type": "text",
                            "text": content,
                            "cache_control": {"type": "ephemeral"}
                        }
                    ]
                elif isinstance(content, list) and len(content) > 0:
                    # Add cache_control to last block
                    last_block = content[-1]
                    if isinstance(last_block, dict):
                        last_block["cache_control"] = {"type": "ephemeral"}
                break
        
        return cached

    def _build_action_metadata(
        self,
        tool_name: str,
        result: Any
    ) -> Dict[str, Any]:
        """
        Build action metadata in Fable-app expected format.

        Issue 3 Fix: Fable expects:
        {
            "id": "action-uuid",
            "type": "note_created" | "note_appended" | "reminder_created" | etc,
            "title": "Title of the item",
            "entity_id": "uuid of created/modified entity",
            "preview": "First 100 chars of content"
        }
        """
        from uuid import uuid4

        # Default action
        action = {
            "id": str(uuid4()),
            "type": tool_name,
            "tool": tool_name,  # Keep for backwards compatibility
            "title": "",
            "entity_id": "",
            "preview": "",
            "status": "",
            "success": True
        }

        # Extract details from result if it's a dict
        if isinstance(result, dict):
            # Map tool status to action type
            status = result.get("status", "")
            success = result.get("success", None)
            if status in ("error", "failed"):
                action["success"] = False
            if success is False:
                action["success"] = False
            action["status"] = status or ("error" if not action["success"] else "ok")
            if tool_name == "smart_save" or tool_name == "save_note":
                if status == "created":
                    action["type"] = "note_created"
                elif status == "appended":
                    action["type"] = "note_appended"
                else:
                    action["type"] = "note_error"

            elif tool_name == "create_reminder":
                action["type"] = "reminder_created"

            elif tool_name == "append_to_note":
                action["type"] = "note_appended"
            elif tool_name == "delete_note":
                action["type"] = "note_deleted"
            elif tool_name == "complete_reminder":
                action["type"] = "reminder_completed"
            elif tool_name == "search_notes":
                action["type"] = "notes_searched"
            elif tool_name == "memory_search":
                action["type"] = "memory_searched"
            elif tool_name.startswith("web_"):
                action["type"] = "web_searched"

            # Extract common fields
            if "note_id" in result:
                action["entity_id"] = result["note_id"]
            elif "reminder_id" in result:
                action["entity_id"] = result["reminder_id"]
            elif "id" in result:
                action["entity_id"] = result["id"]

            if "title" in result:
                action["title"] = result["title"]
            elif "task" in result:
                action["title"] = result["task"]
            elif "topic" in result:
                action["title"] = result["topic"]

            if "content" in result:
                action["preview"] = result["content"][:100]
            elif "message" in result:
                action["preview"] = result["message"][:100]
            elif "results" in result and isinstance(result["results"], list):
                action["preview"] = f"Found {len(result['results'])} results"

        elif isinstance(result, list):
            action["preview"] = f"Found {len(result)} results"

        return action


    # Constants from Clawdbot for progressive compaction
    MAX_COMPACTION_RETRIES = 3
    COMPACTION_RATIOS = [0.6, 0.4, 0.2]  # Progressive truncation ratios

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(Exception),
        before_sleep=lambda retry_state: logger.warning(
            f"Retry attempt {retry_state.attempt_number} after error"
        )
    )
    async def _call_claude_with_retry(
        self,
        messages: List[Dict[str, Any]],
        system: str,
        tools: Optional[List[Dict[str, Any]]] = None,
        _compaction_attempt: int = 0
    ) -> AgentResponse:
        """
        Call Claude API with retry logic and progressive compaction recovery.

        Uses error classification from Clawdbot to determine retry strategy:
        - Context overflow: Progressive compaction (60% -> 40% -> 20%)
        - Rate limit: Exponential backoff (handled by @retry decorator)
        - Auth/Billing: Fail fast with user-friendly message
        """
        client = await self._get_client()

        try:
            # Add cache_control to last user message (like pi-agent)
            cached_messages = self._add_cache_control_to_messages(messages)

            # Use cached system prompt format (like pi-agent)
            cached_system = [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"}
                }
            ]

            response = await client.messages.create(
                model=settings.CLAUDE_MODEL,
                max_tokens=settings.RESERVE_RESPONSE_TOKENS,
                system=cached_system,
                messages=cached_messages,
                tools=tools or []
            )

            # Extract content
            content = ""
            tool_calls = []

            for block in response.content:
                if block.type == "text":
                    content += block.text
                elif block.type == "tool_use":
                    tool_calls.append({
                        "id": block.id,
                        "name": block.name,
                        "input": block.input
                    })

            # Filter thinking blocks from content
            content = filter_thinking_blocks(content)

            # Track cache usage for monitoring (like pi-agent)
            cache_read = getattr(response.usage, 'cache_read_input_tokens', 0) or 0
            cache_write = getattr(response.usage, 'cache_creation_input_tokens', 0) or 0

            if cache_read > 0:
                logger.info(f"Cache hit! Read {cache_read} tokens from cache")
            if cache_write > 0:
                logger.info(f"Cache write: {cache_write} tokens cached")

            return AgentResponse(
                content=content,
                tool_calls=tool_calls,
                usage={
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                    "cache_read_tokens": cache_read,
                    "cache_write_tokens": cache_write
                },
                stop_reason=response.stop_reason
            )

        except Exception as e:
            # Use error classification from Clawdbot
            error_type = classify_error(e)

            if error_type == ErrorType.CONTEXT_OVERFLOW:
                if _compaction_attempt >= self.MAX_COMPACTION_RETRIES:
                    raise CompactionFailedError(
                        f"Context overflow after {self.MAX_COMPACTION_RETRIES} compaction attempts. "
                        f"{get_user_friendly_message(error_type)}"
                    )

                # Progressive truncation (Clawdbot pattern)
                ratio = self.COMPACTION_RATIOS[_compaction_attempt]
                logger.warning(
                    f"Context overflow detected - compacting to {ratio*100:.0f}% "
                    f"(attempt {_compaction_attempt + 1}/{self.MAX_COMPACTION_RETRIES})"
                )

                # Truncate messages more aggressively with each attempt
                truncated = self._context.truncate_to_fit(
                    messages,
                    max_tokens=int(settings.MAX_CONTEXT_TOKENS * ratio)
                )

                # Retry with truncated messages
                return await self._call_claude_with_retry(
                    messages=truncated,
                    system=system,
                    tools=tools,
                    _compaction_attempt=_compaction_attempt + 1
                )

            # Log with user-friendly message for other error types
            user_msg = get_user_friendly_message(error_type)
            logger.error(f"Claude API error ({error_type.value}): {e} - {user_msg}")
            raise
    
    def _default_system_prompt(self) -> str:
        """Default system prompt when none provided."""
        return """You are Fable, a personal AI assistant.

## Core Behaviors
1. Be concise and helpful
2. Use tools when needed to take action
3. Always search before creating (avoid duplicates)
4. Confirm actions were taken

## Available Actions
- Save notes and information
- Create reminders and tasks
- Search user's saved content
- Answer questions using context

Respond naturally but efficiently."""


# Factory
def get_agent_runner(
    session_manager: SessionManager,
    tool_registry: Optional[Any] = None,
    prompt_builder: Optional[Any] = None
) -> AgentRunner:
    """Create an AgentRunner instance."""
    return AgentRunner(session_manager, tool_registry, prompt_builder)
