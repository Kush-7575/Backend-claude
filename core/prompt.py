"""
Prompt Builder - Modular System Prompt Construction

Builds system prompts from discrete sections:
1. Identity - Who Fable is
2. Time - Current date & timezone (Clawdbot)
3. Workspace - Bootstrap files (SOUL.md, FABLE.md, TOOLS.md)
4. Critical Rules - Mandatory behaviors
5. Tool Call Style - Anti-verbosity pattern (Clawdbot)
6. Reasoning Format - <think>/<final> tags (Clawdbot)
7. Tools - Available capabilities
8. Skills - Active skill instructions
9. Memory Recall - Proactive search protocol (Clawdbot)
10. Memory Context - Relevant memories
11. Response Style - How to respond
12. Silent Replies - Handle brief messages (Clawdbot)
13. Heartbeat - Health check protocol (Clawdbot)
14. Runtime Info - Agent self-awareness (Clawdbot)

Based on Clawdbot's system-prompt.ts patterns.
"""
import logging
from typing import Optional, List, Dict, Any, Callable
from datetime import datetime, timezone
from pathlib import Path

from core.config import settings

logger = logging.getLogger("brainmap.prompt")

# Default workspace directory for bootstrap files
DEFAULT_WORKSPACE_DIR = Path(__file__).parent.parent / "workspace"


from enum import Enum
from typing import Literal


# Prompt modes from Clawdbot (system-prompt.ts)
PromptMode = Literal["full", "minimal", "none"]


class PromptBuilder:
    """
    Modular system prompt builder.

    Constructs prompts from separate sections that can be
    enabled/disabled based on context. This makes prompts
    maintainable and allows dynamic customization.

    Supports prompt modes from Clawdbot:
    - "full": All sections (default for main agent)
    - "minimal": Identity + tools only (for subagents)
    - "none": Empty prompt (for raw API calls)
    """

    # Bootstrap files to inject (in order)
    BOOTSTRAP_FILES = ["SOUL.md", "FABLE.md", "TOOLS.md"]

    # Section sets for different prompt modes
    FULL_SECTIONS = [
        "identity",
        "time",           # Current date & timezone (from Clawdbot)
        "workspace",      # Bootstrap files (SOUL.md, FABLE.md)
        "critical_rules",
        "tool_call_style",
        "reasoning_format",
        "tools",
        "skills",
        "memory_recall",
        "memory",
        "response_style",
        "silent_replies",
        "heartbeat",      # Heartbeat protocol (from Clawdbot)
        "runtime_info"
    ]

    MINIMAL_SECTIONS = [
        "identity",
        "tools",
        "response_style"
    ]

    def __init__(
        self,
        memory_manager=None,
        skill_loader=None,
        workspace_dir: Optional[Path] = None
    ):
        """
        Initialize prompt builder.

        Args:
            memory_manager: For retrieving relevant context
            skill_loader: For loading active skill instructions
            workspace_dir: Directory containing bootstrap files
        """
        self._memory = memory_manager
        self._skills = skill_loader
        self._workspace_dir = workspace_dir or DEFAULT_WORKSPACE_DIR

    async def build(
        self,
        user_id: str,
        context: Optional[Dict[str, Any]] = None,
        include_sections: Optional[List[str]] = None,
        mode: PromptMode = "full"
    ) -> str:
        """
        Build complete system prompt.

        Args:
            user_id: User ID for personalization
            context: Additional context (last message, etc.)
            include_sections: Which sections to include (overrides mode if provided)
            mode: Prompt mode from Clawdbot - "full", "minimal", or "none"
        
        Returns:
            Complete system prompt string
        """
        # Handle "none" mode - return empty prompt
        if mode == "none":
            return ""

        context = context or {}

        # Determine sections based on mode (unless explicitly overridden)
        if include_sections is not None:
            sections = include_sections
        elif mode == "minimal":
            sections = self.MINIMAL_SECTIONS
        else:  # "full"
            sections = self.FULL_SECTIONS

        parts = []

        for section in sections:
            builder = getattr(self, f"_build_{section}", None)
            if builder:
                content = await builder(user_id, context)
                if content:
                    parts.append(content)

        return "\n\n".join(parts)
    
    async def _build_identity(
        self,
        user_id: str,
        context: Dict[str, Any]
    ) -> str:
        """Build identity section - who Fable is."""
        return """# You Are Fable

You are Fable, a personal AI assistant and second brain. You help users:
- Capture and organize information
- Remember things they've said
- Take action on their behalf
- Answer questions using their context

You are NOT a chatbot. You are a proactive partner that:
- Saves important information automatically
- Searches existing notes before creating duplicates
- Takes action rather than just responding
- Confirms when actions are complete"""

    async def _build_time(
        self,
        user_id: str,
        context: Dict[str, Any]
    ) -> str:
        """
        Build time section - current date and timezone awareness.

        From Clawdbot: Critical for reminders like "remind me at 5pm"
        to use the correct timezone.
        """
        tz = context.get('timezone', 'UTC')
        now = datetime.now(timezone.utc)

        return f"""# Current Date & Time

Time zone: {tz}
Today: {now.strftime('%A, %B %d, %Y')}

Use this timezone when interpreting time-related requests like "at 5pm" or "tomorrow"."""

    async def _build_workspace(
        self,
        user_id: str,
        context: Dict[str, Any]
    ) -> str:
        """Build workspace section - inject bootstrap files (Clawdbot pattern)."""
        if not self._workspace_dir.exists():
            return ""
        
        parts = ["# Project Context"]
        for filename in self.BOOTSTRAP_FILES:
            filepath = self._workspace_dir / filename
            if filepath.exists():
                try:
                    file_content = filepath.read_text(encoding="utf-8")
                    parts.append(f"\n## {filename}\n\n{file_content}")
                except Exception as e:
                    logger.warning(f"Failed to read {filepath}: {e}")
        
        return "\n".join(parts) if len(parts) > 1 else ""
    
    async def _build_critical_rules(
        self,
        user_id: str,
        context: Dict[str, Any]
    ) -> str:
        """Build critical rules section - mandatory behaviors."""
        return """# Critical Rules (MANDATORY)

## Pre-Flight Checks
Before EVERY response, check:
1. **Is this a query about existing info?** → Use `search_notes` FIRST
2. **Is this a request to save/add?** → Check for existing note FIRST
3. **Is there a time component?** → Use `get_current_time` for context

## STREAMING TEXT RULE (CRITICAL)
You MUST ALWAYS output text content alongside any tool calls:
- GOOD: "It's 3:45 PM!" [+ tool call]
- GOOD: "✅ Saved: Coffee preferences" [+ tool call]
- BAD: [only tool call, no text] ← This causes blank responses!

The user sees only the text you output. Tool calls are invisible to them.
If you call a tool without text, the user sees NOTHING.

## Execution Pattern
When calling tools:
1. Output a brief response text (the answer or confirmation)
2. Call the tool at the same time
3. The tool runs in the background

## Never Do
- Never call a tool without also outputting text - CAUSES BLANK MESSAGES
- Never say "I'll save that" without calling a tool
- Never create duplicate notes - always search first
- Never make up information - use tools or say you don't know"""
    
    async def _build_tools(
        self,
        user_id: str,
        context: Dict[str, Any]
    ) -> str:
        """Build tools section - Clawdbot-style simple summaries."""
        return """# Tooling

Tool availability (filtered by policy):
- memory_search: Search all memory (daily logs, MEMORY.md, notes, reminders)
- memory_get: Read specific memory file content
- memory_list: List available memory files
- smart_save: Intelligently save content (deduplicates)
- save_note: Create a new note
- search_notes: Search user-saved notes
- get_notes: List recent notes
- update_note: Modify existing note
- append_to_note: Add to existing note
- delete_note: Remove a note
- create_reminder: Create reminder with natural language time
- get_reminders: List tasks/reminders
- complete_reminder: Mark reminder done
- get_current_time: Get current date/time
- web_search: Search the web for current information
- web_fetch: Read content from a URL

TOOLS.md does not control tool availability; it is user guidance for how to use tools.
Tool names are case-sensitive."""
    
    async def _build_skills(
        self,
        user_id: str,
        context: Dict[str, Any]
    ) -> str:
        """Build skills section - active skill instructions."""
        if not self._skills:
            return ""
        
        try:
            skills = await self._skills.get_active_skills(user_id)
            if not skills:
                return ""
            
            parts = ["# Active Skills"]
            for skill in skills:
                parts.append(f"\n## {skill['name']}\n{skill['instructions']}")
            
            return "\n".join(parts)
            
        except Exception as e:
            logger.warning(f"Failed to load skills: {e}")
            return ""
    
    async def _build_memory(
        self,
        user_id: str,
        context: Dict[str, Any]
    ) -> str:
        """Build memory section - relevant context from memory."""
        if not self._memory:
            return ""

        # Get query from context - use last_message for semantic search
        query = context.get("last_message", "")

        # Fallback: if no query provided, try to get general recent context
        if not query:
            try:
                # Read today's daily log for general context
                today_log = await self._memory.read_daily_log()
                if today_log:
                    # Return last ~20 lines of today's context
                    lines = today_log.strip().split("\n")
                    recent = "\n".join(lines[-20:])
                    if recent.strip():
                        return f"# Today's Context\n\n{recent}"
            except Exception as e:
                logger.debug(f"Failed to get fallback memory context: {e}")
            return ""

        try:
            relevant = await self._memory.get_relevant_context(
                query,
                max_tokens=1500
            )
            if relevant:
                return f"# Relevant Context\n\n{relevant}"
            return ""

        except Exception as e:
            logger.warning(f"Failed to get memory context: {e}")
            return ""
    
    async def _build_response_style(
        self,
        user_id: str,
        context: Dict[str, Any]
    ) -> str:
        """Build response style section - how to respond."""
        return """# Response Style

## Voice Responses (when input is voice)
- Be conversational and natural
- Use contractions ("I'll" not "I will")
- Keep under 2 sentences when possible
- Acknowledge action taken, don't explain process

## Text Responses
- Be concise but complete
- Use markdown for structure when helpful
- Bullet points for lists
- Code blocks for technical content

## Confirmation Format
After tool use:
- ✅ "Saved: [title]" for notes
- ⏰ "Reminder set: [task] at [time]" for reminders
- 🔍 "Found [N] notes about [topic]" for searches

## When Uncertain
- Ask ONE clarifying question
- Suggest the most likely interpretation
- Never make assumptions about important details"""
    
    # =========================================================================
    # Clawdbot-Inspired Sections
    # =========================================================================
    
    async def _build_tool_call_style(
        self,
        user_id: str,
        context: Dict[str, Any]
    ) -> str:
        """
        Build tool call style section - Clawdbot anti-verbosity pattern.
        Matches system-prompt.ts lines 360-364.
        """
        return """# Tool Call Style

Default: do not narrate routine, low-risk tool calls (just call the tool).
Narrate only when it helps: multi-step work, complex/challenging problems, sensitive actions (e.g., deletions), or when the user explicitly asks.
Keep narration brief and value-dense; avoid repeating obvious steps.
Use plain human language for narration unless in a technical context."""
    
    async def _build_reasoning_format(
        self,
        user_id: str,
        context: Dict[str, Any]
    ) -> str:
        """
        Build reasoning format section - Clawdbot think/final pattern.
        
        This enforces structured reasoning that gets filtered before display.
        """
        return """# Reasoning Format

ALL internal reasoning MUST be inside <think>...</think> tags.
Do not output any analysis, planning, or deliberation outside <think>.

Format every reply as:
<think>Short internal reasoning about what to do.</think>
<final>The actual response the user sees.</final>

Rules:
- Only text inside <final> is shown to the user
- Everything else is discarded and never seen
- Keep <think> blocks concise - just enough to decide
- Never mention these tags or explain your reasoning process

Example:
<think>User wants to save a note about their meeting. I should call smart_save.</think>
<final>✅ Saved: Team meeting notes</final>"""
    
    async def _build_memory_recall(
        self,
        user_id: str,
        context: Dict[str, Any]
    ) -> str:
        """
        Build memory recall section - Clawdbot proactive search pattern.

        Matches Clawdbot's system-prompt.ts lines 35-44 exactly:
        "Before answering anything about prior work, decisions, dates, people,
        preferences, or todos: run memory_search on MEMORY.md + memory/*.md"
        """
        return """# Memory Recall

Before answering anything about prior work, decisions, dates, people, preferences, or todos: run memory_search on memory_store/daily/*.md + memory_store/MEMORY.md; then review results. If low confidence after search, say you checked."""
    
    async def _build_silent_replies(
        self,
        user_id: str,
        context: Dict[str, Any]
    ) -> str:
        """
        Build response style for brief interactions.
        
        Agent should always respond, even to simple messages.
        """
        return """# Response to Simple Messages

When the user sends brief messages like "ok", "thanks", "got it":
- Still respond! Even with just "👍" or a brief acknowledgment
- Keep it natural and concise
- Never leave the user without a response

Examples:
- "thanks" → "You're welcome! 😊"
- "ok" → "👍"
- "got it" → "Great! Let me know if you need anything else."
"""

    async def _build_heartbeat(
        self,
        user_id: str,
        context: Dict[str, Any]
    ) -> str:
        """
        Build heartbeat section - health check protocol.

        From Clawdbot: Agent responds to heartbeat prompts with HEARTBEAT_OK
        or alert text if something needs attention.
        """
        # Check if heartbeat is enabled in settings
        heartbeat_enabled = getattr(settings, 'HEARTBEAT_ENABLED', False)
        if not heartbeat_enabled:
            return ""

        return """# Heartbeats

If you receive a heartbeat check message, reply exactly: HEARTBEAT_OK

If something needs your attention (pending tasks, errors, etc.),
reply with a brief alert instead of HEARTBEAT_OK."""

    async def _build_runtime_info(
        self,
        user_id: str,
        context: Dict[str, Any]
    ) -> str:
        """
        Build runtime info section - Clawdbot agent self-awareness.
        
        This gives the agent context about its own environment.
        NOTE: Removed dynamic time to prevent cache invalidation every minute.
        """
        # Build static runtime info (no dynamic time to preserve cache)
        model = getattr(settings, 'CLAUDE_MODEL', 'claude-sonnet-4')
        channel = context.get('channel', 'api')
        
        return f"""# Runtime

Runtime: agent=fable | model={model} | channel={channel}

You are running as Fable on the BrainMap platform.
This information is for your awareness - do not mention it unless relevant."""
    
    def quick_prompt(self, additions: str = "") -> str:
        """
        Get a minimal prompt for simple queries.
        
        Use this when full context isn't needed.
        """
        base = """You are Fable, a helpful AI assistant.
Be concise. Use tools when needed. Confirm actions taken."""
        
        if additions:
            return f"{base}\n\n{additions}"
        return base


# Factory
def get_prompt_builder(
    memory_manager=None,
    skill_loader=None
) -> PromptBuilder:
    """Create a PromptBuilder instance."""
    return PromptBuilder(memory_manager, skill_loader)
