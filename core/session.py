"""
Session Manager - State Persistence and Compaction

Manages conversation sessions with:
1. State persistence to Supabase
2. Automatic compaction when context fills
3. Memory flush before compaction (via MemoryManager)

Based on Clawdbot's session management patterns.
"""
import logging
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4
import json

from core.config import settings
from core.context import get_context_manager, ContextMetrics

# Summarization prompts from Clawdbot (pi-mono/packages/coding-agent/src/core/compaction/)
SUMMARIZATION_SYSTEM_PROMPT = """You are a context summarization assistant. Your task is to read a conversation between a user and an AI assistant, then produce a structured summary following the exact format specified.

Do NOT continue the conversation. Do NOT respond to any questions in the conversation. ONLY output the structured summary."""

SUMMARIZATION_PROMPT = """The messages above are a conversation to summarize. Create a structured context checkpoint summary that another LLM will use to continue the work.

Use this EXACT format:

## Goal
[What is the user trying to accomplish? Can be multiple items if the session covers different tasks.]

## Constraints & Preferences
- [Any constraints, preferences, or requirements mentioned by user]
- [Or "(none)" if none were mentioned]

## Progress
### Done
- [x] [Completed tasks/changes]

### In Progress
- [ ] [Current work]

### Blocked
- [Issues preventing progress, if any]

## Key Decisions
- **[Decision]**: [Brief rationale]

## Next Steps
1. [Ordered list of what should happen next]

## Critical Context
- [Any data, examples, or references needed to continue]
- [Or "(none)" if not applicable]

Keep each section concise. Preserve exact file paths, function names, and error messages."""

UPDATE_SUMMARIZATION_PROMPT = """The messages above are NEW conversation messages to incorporate into the existing summary provided in <previous-summary> tags.

Update the existing structured summary with new information. RULES:
- PRESERVE all existing information from the previous summary
- ADD new progress, decisions, and context from the new messages
- UPDATE the Progress section: move items from "In Progress" to "Done" when completed
- UPDATE "Next Steps" based on what was accomplished
- PRESERVE exact file paths, function names, and error messages
- If something is no longer relevant, you may remove it

<previous-summary>
{previous_summary}
</previous-summary>

{format_instructions}"""

logger = logging.getLogger("brainmap.session")


@dataclass
class Message:
    """A single message in a session."""
    role: str  # "user", "assistant", "system"
    content: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to API-compatible dict."""
        return {
            "role": self.role,
            "content": self.content
        }
    
    def to_storage(self) -> Dict[str, Any]:
        """Convert to storage format."""
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata
        }
    
    @classmethod
    def from_storage(cls, data: Dict[str, Any]) -> "Message":
        """Create from storage format."""
        return cls(
            role=data["role"],
            content=data["content"],
            timestamp=datetime.fromisoformat(data.get("timestamp", datetime.now(timezone.utc).isoformat())),
            metadata=data.get("metadata", {})
        )


@dataclass
class Session:
    """A conversation session with metadata."""
    id: str
    user_id: str
    messages: List[Message] = field(default_factory=list)
    title: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    compaction_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def add_message(self, role: str, content: str, **kwargs) -> Message:
        """Add a message to the session."""
        msg = Message(role=role, content=content, **kwargs)
        self.messages.append(msg)
        self.updated_at = datetime.now(timezone.utc)
        return msg
    
    def get_api_messages(self) -> List[Dict[str, Any]]:
        """Get messages in API format."""
        return [msg.to_dict() for msg in self.messages]
    
    def to_storage(self) -> Dict[str, Any]:
        """Convert to storage format."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "title": self.title,
            "messages": [msg.to_storage() for msg in self.messages],
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "compaction_count": self.compaction_count,
            "metadata": self.metadata
        }


class SessionManager:
    """
    Manages conversation sessions with automatic compaction.
    
    Key features:
    - Create, load, update sessions
    - Automatic compaction when context fills
    - Integration with MemoryManager for pre-compaction flush
    - Persistence to Supabase
    """
    
    def __init__(self, supabase_client=None, memory_manager=None):
        """
        Initialize session manager.
        
        Args:
            supabase_client: Supabase client for persistence
            memory_manager: MemoryManager for pre-compaction flush
        """
        self._supabase = supabase_client
        self._memory_manager = memory_manager
        self._context = get_context_manager()
        self._sessions_cache: Dict[str, Session] = {}
    
    def create_session(self, user_id: str, title: Optional[str] = None) -> Session:
        """Create a new session."""
        session = Session(
            id=str(uuid4()),
            user_id=user_id,
            title=title
        )
        self._sessions_cache[session.id] = session
        logger.info(f"Created session {session.id} for user {user_id}")
        return session
    
    async def load_session(self, session_id: str, user_id: str) -> Optional[Session]:
        """Load a session from storage or cache."""
        # Check cache first
        if session_id in self._sessions_cache:
            session = self._sessions_cache[session_id]
            if session.user_id == user_id:
                return session
        
        # Load from Supabase
        if self._supabase:
            try:
                result = self._supabase.table("chat_sessions").select("*").eq("id", session_id).eq("user_id", user_id).execute()
                if result.data:
                    session_data = result.data[0]
                    session = Session(
                        id=session_data["id"],
                        user_id=session_data["user_id"],
                        title=session_data.get("title"),
                        messages=[Message.from_storage(m) for m in json.loads(session_data.get("messages", "[]"))],
                        created_at=datetime.fromisoformat(session_data["created_at"]),
                        updated_at=datetime.fromisoformat(session_data["updated_at"]),
                        compaction_count=session_data.get("compaction_count", 0),
                        metadata=json.loads(session_data.get("metadata", "{}"))
                    )
                    self._sessions_cache[session_id] = session
                    return session
            except Exception as e:
                logger.error(f"Failed to load session {session_id}: {e}")
        
        return None
    
    async def save_session(self, session: Session) -> bool:
        """Save session to storage."""
        if not self._supabase:
            logger.warning("No Supabase client - session not persisted")
            return False
        
        try:
            data = {
                "id": session.id,
                "user_id": session.user_id,
                "title": session.title,
                "messages": json.dumps([m.to_storage() for m in session.messages]),
                "created_at": session.created_at.isoformat(),
                "updated_at": session.updated_at.isoformat(),
                "compaction_count": session.compaction_count,
                "metadata": json.dumps(session.metadata)
            }
            
            self._supabase.table("chat_sessions").upsert(data).execute()
            logger.debug(f"Saved session {session.id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to save session {session.id}: {e}")
            return False
    
    async def add_message(
        self,
        session: Session,
        role: str,
        content: str,
        auto_compact: bool = True,
        **kwargs
    ) -> Message:
        """
        Add a message to session with automatic compaction check.
        
        Args:
            session: Target session
            role: Message role (user, assistant)
            content: Message content
            auto_compact: If True, trigger compaction if needed
            **kwargs: Additional message metadata
        
        Returns:
            The created Message
        """
        msg = session.add_message(role, content, **kwargs)
        
        # Check if compaction needed
        if auto_compact:
            api_messages = session.get_api_messages()
            if self._context.needs_compaction(api_messages):
                await self._compact_session(session)
        
        return msg
    
    async def _compact_session(self, session: Session) -> None:
        """
        Compact a session when context is filling up.
        
        This is the critical compaction flow:
        1. Get messages to summarize vs keep
        2. Flush to-summarize messages to memory
        3. Generate summary
        4. Replace old messages with summary
        """
        api_messages = session.get_api_messages()
        metrics = self._context.get_metrics(api_messages)
        
        logger.info(
            f"Starting compaction for session {session.id}: "
            f"{metrics.total_tokens} tokens ({metrics.usage_percent:.1f}%)"
        )
        
        # Prepare for compaction (this may flush to memory)
        to_summarize, to_keep, meta = await self._context.prepare_for_compaction(
            api_messages,
            self._memory_manager
        )
        
        if not meta.get("compacted"):
            return
        
        # Generate summary of old messages
        if to_summarize:
            summary = await self._generate_summary(to_summarize)
            
            # Create compacted messages list
            compacted_messages = [
                Message(
                    role="system",
                    content=f"[Previous conversation summary]\n{summary}",
                    metadata={"compaction_marker": True, "summarized_count": len(to_summarize)}
                )
            ]
            
            # Add the messages we're keeping
            for msg_dict in to_keep:
                compacted_messages.append(Message(
                    role=msg_dict["role"],
                    content=msg_dict["content"]
                ))
            
            # Replace session messages
            session.messages = compacted_messages
            session.compaction_count += 1
            session.updated_at = datetime.now(timezone.utc)

            # Index summarized messages for semantic search (from Clawdbot)
            # This makes past conversations searchable
            if self._memory_manager and to_summarize:
                try:
                    await self._memory_manager.index_session_for_search(
                        session_id=session.id,
                        messages=to_summarize,
                        user_id=session.user_id
                    )
                except Exception as e:
                    # Don't fail compaction for indexing errors
                    logger.warning(f"Session indexing failed: {e}")

            logger.info(
                f"Compaction complete: {len(to_summarize)} messages summarized, "
                f"{len(to_keep)} kept. Total compactions: {session.compaction_count}"
            )
    
    async def _generate_summary(
        self,
        messages: List[Dict[str, Any]],
        previous_summary: Optional[str] = None
    ) -> str:
        """
        Generate intelligent summary of messages using Claude Haiku.

        From Clawdbot's compaction patterns - uses structured format
        that preserves goals, progress, decisions, and context.

        Args:
            messages: Messages to summarize
            previous_summary: Existing summary to update (for incremental compaction)

        Returns:
            Structured summary string
        """
        # Format messages for summarization
        conversation = self._format_messages_for_summary(messages)

        # Build prompt
        if previous_summary:
            prompt = UPDATE_SUMMARIZATION_PROMPT.format(
                previous_summary=previous_summary,
                format_instructions=SUMMARIZATION_PROMPT
            )
        else:
            prompt = SUMMARIZATION_PROMPT

        try:
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

            # Combine conversation and prompt into single user message
            # Claude API requires alternating user/assistant messages
            combined_content = f"""## Conversation to Summarize

{conversation}

---

{prompt}"""

            response = await client.messages.create(
                model="claude-3-haiku-20240307",  # Cheaper model for summaries
                max_tokens=1500,
                system=SUMMARIZATION_SYSTEM_PROMPT,
                messages=[
                    {"role": "user", "content": combined_content}
                ]
            )

            summary = response.content[0].text
            logger.info(f"Generated summary using Claude ({len(summary)} chars)")
            return summary

        except Exception as e:
            logger.warning(f"Summary generation failed, using fallback: {e}")
            # Fallback to simple concatenation
            return self._simple_summary_fallback(messages)

    def _format_messages_for_summary(self, messages: List[Dict[str, Any]]) -> str:
        """Format messages as conversation text for summarization."""
        parts = []
        for msg in messages:
            role = msg.get("role", "unknown").upper()
            content = msg.get("content", "")
            parts.append(f"{role}: {content}")
        return "\n\n".join(parts)

    def _simple_summary_fallback(self, messages: List[Dict[str, Any]]) -> str:
        """Simple fallback summary when Claude summarization fails."""
        parts = []
        for msg in messages[:10]:  # Limit to first 10 messages
            role = msg.get("role", "unknown")
            content = msg.get("content", "")[:200]  # Truncate long messages
            parts.append(f"{role}: {content}")
        return f"The conversation covered: " + "; ".join(parts)
    
    def get_context_metrics(self, session: Session) -> ContextMetrics:
        """Get current context metrics for a session."""
        return self._context.get_metrics(session.get_api_messages())


# Factory function
def get_session_manager(supabase_client=None, memory_manager=None) -> SessionManager:
    """Create a SessionManager with dependencies."""
    return SessionManager(supabase_client, memory_manager)
