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

Performance optimizations (from Clawdbot):
- Workspace file cache with mtime-based invalidation
- Parallel prompt section building
- Token count memoization
"""
import logging
import re
import asyncio
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Callable, Tuple
from pathlib import Path
from time import time as time_now

from core.config import settings
from tools.registry import get_tool_registry
from tools.policy import get_tool_policy
from core.context import get_context_manager


# =============================================================================
# Workspace File Cache (from Clawdbot session-manager-cache.ts pattern)
# =============================================================================

# Cache entry: (mtime, content, loaded_at)
_workspace_file_cache: Dict[str, Tuple[float, str, float]] = {}
WORKSPACE_CACHE_TTL_SECONDS = 45  # Matches Clawdbot's 45s TTL


def _is_workspace_cache_valid(filepath: str) -> bool:
    """Check if cached workspace file is still valid."""
    if filepath not in _workspace_file_cache:
        return False
    mtime, content, loaded_at = _workspace_file_cache[filepath]
    
    # Check TTL
    if time_now() - loaded_at > WORKSPACE_CACHE_TTL_SECONDS:
        return False
    
    # Check mtime hasn't changed
    try:
        current_mtime = Path(filepath).stat().st_mtime
        return current_mtime == mtime
    except (OSError, FileNotFoundError):
        return False


def _read_workspace_file_cached(filepath: Path) -> str:
    """
    Read workspace file with mtime-based cache (Clawdbot pattern).
    
    This eliminates redundant file I/O for bootstrap files that
    rarely change during a session.
    """
    filepath_str = str(filepath)
    
    # Check cache validity
    if _is_workspace_cache_valid(filepath_str):
        _, content, _ = _workspace_file_cache[filepath_str]
        return content
    
    # Cache miss - read file
    try:
        content = filepath.read_text(encoding="utf-8")
        mtime = filepath.stat().st_mtime
        _workspace_file_cache[filepath_str] = (mtime, content, time_now())
        return content
    except (OSError, FileNotFoundError):
        return ""


def clear_workspace_cache() -> None:
    """Clear the workspace file cache (for testing)."""
    _workspace_file_cache.clear()


# Cache TTL constants (from Clawdbot cache-ttl.ts)
CACHE_TTL_INTERVAL_SECONDS = 300  # 5 minutes - prompts with same timestamp hit cache
CACHE_TTL_ELIGIBLE_PROVIDERS = {"anthropic", "openai"}  # Providers that support prompt caching


def is_cache_ttl_eligible_provider(provider: str) -> bool:
    """Check if provider supports prompt caching."""
    return provider.lower() in CACHE_TTL_ELIGIBLE_PROVIDERS


def get_cache_ttl_timestamp() -> str:
    """
    Get a timestamp for cache TTL bucketing.
    
    From Clawdbot's appendCacheTtlTimestamp - rounds to 5-minute intervals
    so prompts within the same window will hit cache.
    
    Returns:
        Timestamp comment string to append to system prompt
    """
    now = datetime.now(timezone.utc)
    # Round to nearest 5-minute interval
    bucket = (now.timestamp() // CACHE_TTL_INTERVAL_SECONDS) * CACHE_TTL_INTERVAL_SECONDS
    bucket_time = datetime.fromtimestamp(bucket, timezone.utc)
    return f"\n\n<!-- cache-ttl: {bucket_time.isoformat()} -->"


def append_cache_ttl_timestamp(
    system_prompt: str,
    provider: str = "anthropic"
) -> str:
    """
    Append cache TTL timestamp to system prompt if eligible.
    
    From Clawdbot's cache-ttl.ts pattern - adds a timestamp comment
    that buckets cache keys by time interval.
    
    Args:
        system_prompt: The system prompt to modify
        provider: The LLM provider being used
        
    Returns:
        System prompt with optional cache TTL timestamp
    """
    if not is_cache_ttl_eligible_provider(provider):
        return system_prompt
    
    return system_prompt + get_cache_ttl_timestamp()

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

    # Tool preference ordering (signal best tools first)
    TOOL_PREFERENCE_ORDER = [
        "memory_search",
        "smart_save",
        "search_notes",
        "get_notes",
        "append_to_note",
        "create_reminder",
        "get_reminders",
        "complete_reminder",
        "web_search",
        "web_research"
    ]

    # Sections eligible for total-budget truncation (lower priority first)
    TOTAL_BUDGET_TRUNCATION_ORDER = [
        "workspace",
        "skills",
        "tools",
        "response_style",
        "runtime_info",
        "heartbeat",
        "silent_replies",
        "reasoning_format",
        "tool_call_style",
        "time"
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
        self._last_skills_version: Optional[int] = None
        self._workspace_file_max_chars = settings.WORKSPACE_FILE_MAX_CHARS
        self._workspace_total_max_chars = settings.WORKSPACE_TOTAL_MAX_CHARS
        self._section_token_budgets = settings.PROMPT_SECTION_TOKEN_BUDGETS or {}
        self._total_token_budget = settings.PROMPT_TOTAL_TOKEN_BUDGET
        self._context = get_context_manager()

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

        section_data = await self._build_sections(user_id, context, sections)
        return "\n\n".join([s["content"] for s in section_data if s["content"]])

    async def build_with_report(
        self,
        user_id: str,
        context: Optional[Dict[str, Any]] = None,
        include_sections: Optional[List[str]] = None,
        mode: PromptMode = "full"
    ) -> Dict[str, Any]:
        """Build prompt and return a report with section token counts."""
        if mode == "none":
            return {"prompt": "", "total_tokens": 0, "sections": []}

        context = context or {}

        if include_sections is not None:
            sections = include_sections
        elif mode == "minimal":
            sections = self.MINIMAL_SECTIONS
        else:
            sections = self.FULL_SECTIONS

        section_data = await self._build_sections(user_id, context, sections)
        prompt = "\n\n".join([s["content"] for s in section_data if s["content"]])
        total_tokens = sum(s["tokens"] for s in section_data if s["content"])
        return {"prompt": prompt, "total_tokens": total_tokens, "sections": section_data}

    # Sections that can be built in parallel (no dependencies)
    # These are pure string builders with no shared state
    PARALLEL_SAFE_SECTIONS = {
        "identity", "time", "critical_rules", "tool_call_style",
        "reasoning_format", "memory_recall", "response_style",
        "silent_replies", "heartbeat", "runtime_info"
    }

    async def _build_sections(
        self,
        user_id: str,
        context: Dict[str, Any],
        sections: List[str]
    ) -> List[Dict[str, Any]]:
        """
        Build prompt sections with parallel execution (Clawdbot Promise.all pattern).
        
        Independent sections are built concurrently using asyncio.gather,
        while order-dependent sections are built sequentially.
        """
        # Separate parallel-safe vs sequential sections while preserving order
        parallel_sections = []
        sequential_sections = []
        section_order = []  # Track original order
        
        for section in sections:
            builder = getattr(self, f"_build_{section}", None)
            if not builder:
                continue
            section_order.append(section)
            if section in self.PARALLEL_SAFE_SECTIONS:
                parallel_sections.append((section, builder))
            else:
                sequential_sections.append((section, builder))
        
        # Build parallel sections concurrently (Clawdbot Promise.all pattern)
        parallel_results: Dict[str, str] = {}
        if parallel_sections:
            tasks = [builder(user_id, context) for _, builder in parallel_sections]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, (section, _) in enumerate(parallel_sections):
                result = results[i]
                if isinstance(result, Exception):
                    logger.warning(f"Section {section} failed: {result}")
                    parallel_results[section] = ""
                else:
                    parallel_results[section] = result or ""
        
        # Build sequential sections one by one
        sequential_results: Dict[str, str] = {}
        for section, builder in sequential_sections:
            try:
                content = await builder(user_id, context)
                sequential_results[section] = content or ""
            except Exception as e:
                logger.warning(f"Section {section} failed: {e}")
                sequential_results[section] = ""
        
        # Merge results in original order
        all_results = {**parallel_results, **sequential_results}
        data: List[Dict[str, Any]] = []
        
        for section in section_order:
            content = all_results.get(section, "")
            if not content:
                continue
            tokens = self._context.count_tokens(content)
            truncated = False
            budget = self._section_token_budgets.get(section)
            if isinstance(budget, int) and budget > 0 and tokens > budget:
                content = self._context.truncate_text_to_tokens(content, budget)
                truncated = True
                tokens = self._context.count_tokens(content)
            data.append({
                "name": section,
                "content": content,
                "tokens": tokens,
                "chars": len(content),
                "truncated": truncated
            })

        if self._total_token_budget and self._total_token_budget > 0:
            data = self._apply_total_budget(data, self._total_token_budget)
        return data

    def _apply_total_budget(
        self,
        data: List[Dict[str, Any]],
        total_budget: int
    ) -> List[Dict[str, Any]]:
        total_tokens = sum(item["tokens"] for item in data)
        if total_tokens <= total_budget:
            return data

        excess = total_tokens - total_budget
        for section in self.TOTAL_BUDGET_TRUNCATION_ORDER:
            if excess <= 0:
                break
            for item in data:
                if item["name"] != section or not item["content"]:
                    continue
                current = item["tokens"]
                if current <= 0:
                    continue
                new_max = max(current - excess, 0)
                new_content = self._context.truncate_text_to_tokens(item["content"], new_max)
                item["content"] = new_content
                item["tokens"] = self._context.count_tokens(new_content)
                item["chars"] = len(new_content)
                item["truncated"] = True
                excess = max(0, excess - (current - item["tokens"]))

        return data
    
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
        return f"""# Current Date & Time

Time zone: {tz}

Use this timezone when interpreting time-related requests like "at 5pm" or "tomorrow"."""

    async def _build_workspace(
        self,
        user_id: str,
        context: Dict[str, Any]
    ) -> str:
        """Build workspace section - inject bootstrap files (Clawdbot pattern)."""
        if not self._workspace_dir.exists():
            return ""

        report = self.get_workspace_report()
        if not report:
            return ""

        parts = ["# Project Context"]
        total_used = 0
        for entry in report:
            if entry["injected_chars"] <= 0:
                continue
            total_used += entry["injected_chars"]
            title = f"{entry['name']}{' (truncated)' if entry['truncated'] else ''}"
            parts.append(f"\n## {title}\n\n{entry['content']}")
            if total_used >= self._workspace_total_max_chars:
                parts.append("\n## Project Context (truncated)\n\n...[TRUNCATED]")
                break

        return "\n".join(parts) if len(parts) > 1 else ""

    def get_workspace_report(self) -> List[Dict[str, Any]]:
        """Get workspace file injection stats for diagnostics."""
        if not self._workspace_dir.exists():
            return []
        report: List[Dict[str, Any]] = []
        total_used = 0
        head_ratio = max(0.0, min(1.0, settings.WORKSPACE_TRIM_HEAD_RATIO))
        tail_ratio = max(0.0, min(1.0, settings.WORKSPACE_TRIM_TAIL_RATIO))
        if head_ratio + tail_ratio > 1.0:
            tail_ratio = max(0.0, 1.0 - head_ratio)

        for filename in self.BOOTSTRAP_FILES:
            filepath = self._workspace_dir / filename
            if not filepath.exists():
                report.append({
                    "name": filename,
                    "path": str(filepath),
                    "missing": True,
                    "raw_chars": 0,
                    "injected_chars": 0,
                    "truncated": False,
                    "content": "",
                })
                continue
            
            # Use cached file reading (Clawdbot pattern)
            content = _read_workspace_file_cached(filepath)
            if not content:
                logger.warning(f"Failed to read {filepath}")
                report.append({
                    "name": filename,
                    "path": str(filepath),
                    "missing": True,
                    "raw_chars": 0,
                    "injected_chars": 0,
                    "truncated": False,
                    "content": "",
                })
                continue

            raw_chars = len(content)
            injected = content
            truncated = False
            if raw_chars > self._workspace_file_max_chars:
                head_chars = int(self._workspace_file_max_chars * head_ratio)
                tail_chars = int(self._workspace_file_max_chars * tail_ratio)
                if head_chars + tail_chars > self._workspace_file_max_chars:
                    tail_chars = max(0, self._workspace_file_max_chars - head_chars)
                head = content[:head_chars] if head_chars > 0 else ""
                tail = content[-tail_chars:] if tail_chars > 0 else ""
                marker = "\n...[TRUNCATED - read file for full content]...\n"
                injected = f"{head}{marker}{tail}".strip()
                truncated = True

            remaining = max(0, self._workspace_total_max_chars - total_used)
            if remaining <= 0:
                injected = ""
            elif len(injected) > remaining:
                head_chars = int(remaining * head_ratio)
                tail_chars = int(remaining * tail_ratio)
                if head_chars + tail_chars > remaining:
                    tail_chars = max(0, remaining - head_chars)
                head = injected[:head_chars] if head_chars > 0 else ""
                tail = injected[-tail_chars:] if tail_chars > 0 else ""
                marker = "\n...[TRUNCATED]...\n"
                injected = f"{head}{marker}{tail}".strip()
                truncated = True

            injected_chars = len(injected)
            total_used += injected_chars

            report.append({
                "name": filename,
                "path": str(filepath),
                "missing": False,
                "raw_chars": raw_chars,
                "injected_chars": injected_chars,
                "truncated": truncated,
                "content": injected,
            })

        return report
    
    async def _build_critical_rules(
        self,
        user_id: str,
        context: Dict[str, Any]
    ) -> str:
        """Build critical rules section - mandatory behaviors."""
        return """# Critical Rules (MANDATORY)

## Pre-Flight Checks
Before EVERY response, check:
1. **Is this a query about existing info?** → Use `memory_search` FIRST
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
        """Build tools section from registered tools filtered by policy."""
        registry = get_tool_registry()
        policy = get_tool_policy()
        allowed = set(policy.resolve(user_id))
        summaries = registry.get_tool_summaries(allowlist=allowed)
        tool_names = [name for name in registry.get_tool_names() if name in allowed]
        tool_lines = []
        for name in sorted(tool_names):
            summary = summaries.get(name, "")
            tool_lines.append(f"- {name}: {summary}" if summary else f"- {name}")

        preferred = [t for t in self.TOOL_PREFERENCE_ORDER if t in allowed]
        preference_line = ""
        if preferred:
            preference_line = "Tool priority: " + " > ".join(preferred)

        return "\n".join([
            "# Tooling",
            "",
            "Tool availability (filtered by policy):",
            "Tool names are case-sensitive. Call tools exactly as listed.",
            preference_line,
            *tool_lines,
            "",
            "TOOLS.md does not control tool availability; it is user guidance for how to use tools."
        ])
    
    async def _build_skills(
        self,
        user_id: str,
        context: Dict[str, Any]
    ) -> str:
        """Build skills section - compact list with lazy skill loading."""
        if not self._skills:
            return ""

        def xml_escape(value: str) -> str:
            return (
                value.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
                .replace("'", "&apos;")
            )

        try:
            tool_registry = get_tool_registry()
            query = context.get("last_message") if isinstance(context, dict) else None
            entries = self._skills.list_available_skills(
                tool_registry=tool_registry,
                query=query
            )
            if not entries:
                return ""

            skills_xml = ["<available_skills>"]
            for entry in entries:
                skills_xml.append("  <skill>")
                skills_xml.append(f"    <name>{xml_escape(entry['name'])}</name>")
                skills_xml.append(
                    f"    <description>{xml_escape(entry['description'])}</description>"
                )
                skills_xml.append(f"    <location>{xml_escape(entry['location'])}</location>")
                skills_xml.append("  </skill>")
            skills_xml.append("</available_skills>")

            return "\n".join([
                "## Skills (mandatory)",
                "Before replying: scan <available_skills> <description> entries.",
                "- If exactly one skill clearly applies: read its SKILL.md at <location> using `skill_read`, then follow it.",
                "- If multiple could apply: choose the most specific one, then read/follow it.",
                "- If none clearly apply: do not read any SKILL.md.",
                "Constraints: never read more than one skill up front; only read after selecting.",
                "",
                *skills_xml,
                "",
            ])
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

        if not self._should_include_memory_context(context):
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

    def _should_include_memory_context(self, context: Dict[str, Any]) -> bool:
        """Gate memory retrieval to reduce latency on short/low-signal messages."""
        if not settings.MEMORY_PREFETCH_ENABLED:
            return False

        if context.get("skip_memory") is True:
            return False

        query = (context.get("last_message") or "").strip().lower()
        if not query:
            return False

        min_chars = max(1, int(settings.MEMORY_PREFETCH_MIN_CHARS))
        max_chars = max(min_chars, int(settings.MEMORY_PREFETCH_MAX_CHARS))
        if len(query) < min_chars:
            return False
        if len(query) > max_chars:
            return True

        if query in {"hi", "hey", "hello", "yo", "sup", "wassup", "what's up"}:
            return False

        if "memory_search" in query or "search notes" in query:
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
            if re.search(pattern, query):
                return True
        return "?" in query and any(
            kw in query for kw in ("past", "previous", "earlier", "last", "before")
        )
    
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

Before answering anything about prior work, decisions, dates, people, preferences, or todos: run memory_search (it searches daily logs, long-term memory, sessions, notes, reminders); then review results. If low confidence after search, say you checked."""
    
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
