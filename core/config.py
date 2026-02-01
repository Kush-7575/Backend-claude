"""
BrainMap Core Configuration

Centralized settings using Pydantic Settings for type-safe configuration.
All settings can be overridden via environment variables.
"""
from typing import List, Optional, Dict
from pydantic_settings import BaseSettings
from pydantic import Field
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    # -------------------------------------------------------------------------
    # Claude API
    # -------------------------------------------------------------------------
    ANTHROPIC_API_KEY: str = Field(
        ...,
        description="Anthropic API key for Claude"
    )
    CLAUDE_MODEL: str = Field(
        default="claude-opus-4-5-20251101",
        description="Claude model to use (Opus 4.5 - maximum intelligence)"
    )
    
    # -------------------------------------------------------------------------
    # Supabase
    # -------------------------------------------------------------------------
    SUPABASE_URL: str = Field(..., description="Supabase project URL")
    SUPABASE_KEY: str = Field(..., description="Supabase anon/service key")
    
    # -------------------------------------------------------------------------
    # Context Management (Critical for Session Compaction)
    # -------------------------------------------------------------------------
    MAX_CONTEXT_TOKENS: int = Field(
        default=150000,
        description="Maximum tokens before triggering compaction"
    )
    RESERVE_RESPONSE_TOKENS: int = Field(
        default=4000,
        description="Tokens reserved for model response"
    )
    KEEP_RECENT_TOKENS: int = Field(
        default=20000,
        description="Tokens to keep from recent messages after compaction"
    )
    COMPACTION_THRESHOLD: float = Field(
        default=0.8,
        description="Trigger compaction at this % of max context"
    )
    
    # -------------------------------------------------------------------------
    # Memory System
    # -------------------------------------------------------------------------
    MEMORY_DIR: str = Field(
        default="./memory_store",
        description="Directory for file-based memory storage"
    )
    WORKSPACE_FILE_MAX_CHARS: int = Field(
        default=2000,
        description="Max characters to inject per workspace file (reduced for efficiency)"
    )
    WORKSPACE_TOTAL_MAX_CHARS: int = Field(
        default=6000,
        description="Max total characters to inject from workspace files (reduced for efficiency)"
    )
    WORKSPACE_TRIM_HEAD_RATIO: float = Field(
        default=0.7,
        description="Head ratio for workspace truncation"
    )
    WORKSPACE_TRIM_TAIL_RATIO: float = Field(
        default=0.2,
        description="Tail ratio for workspace truncation"
    )
    PROMPT_SECTION_TOKEN_BUDGETS: Dict[str, int] = Field(
        default_factory=lambda: {"workspace": 2000, "skills": 1000},
        description="Per-section token budgets for system prompt"
    )
    PROMPT_TOTAL_TOKEN_BUDGET: int = Field(
        default=5000,
        description="Max total tokens for system prompt (5000 = efficient cap)"
    )
    EMBEDDING_PROVIDER: str = Field(
        default="openai",
        description="Vector embedding provider: openai, local, none"
    )
    HYBRID_VECTOR_WEIGHT: float = Field(
        default=0.7,
        description="Hybrid search vector weight"
    )
    HYBRID_TEXT_WEIGHT: float = Field(
        default=0.3,
        description="Hybrid search text weight"
    )
    MEMORY_SEARCH_CACHE_ENABLED: bool = Field(
        default=True,
        description="Enable memory_search result cache"
    )
    MEMORY_SEARCH_CACHE_MAX_ENTRIES: int = Field(
        default=128,
        description="Max entries in memory_search cache"
    )
    MEMORY_SEARCH_CACHE_TTL_SECONDS: int = Field(
        default=180,
        description="TTL for memory_search cache entries"
    )
    MEMORY_PREFETCH_ENABLED: bool = Field(
        default=False,
        description="Enable auto memory prefetch for past-info queries"
    )
    MEMORY_PREFETCH_MIN_CHARS: int = Field(
        default=12,
        description="Minimum user message length for memory prefetch"
    )
    MEMORY_PREFETCH_MAX_CHARS: int = Field(
        default=600,
        description="Maximum user message length for auto memory prefetch"
    )
    OPENAI_API_KEY: Optional[str] = Field(
        default=None,
        description="OpenAI API key for embeddings"
    )
    GOOGLE_API_KEY: Optional[str] = Field(
        default=None,
        description="Google API key for Gemini embeddings (fallback)"
    )
    
    # -------------------------------------------------------------------------
    # Heartbeat / Proactive Features
    # -------------------------------------------------------------------------
    HEARTBEAT_ENABLED: bool = Field(
        default=True,
        description="Enable periodic heartbeat checks"
    )
    HEARTBEAT_INTERVAL_MINUTES: int = Field(
        default=30,
        description="Minutes between heartbeat checks"
    )
    
    # -------------------------------------------------------------------------
    # Development
    # -------------------------------------------------------------------------
    DEV_MODE: bool = Field(
        default=False,
        description="Skip authentication in dev mode"
    )
    CONTEXT_SOFT_TRIM_MAX_CHARS: int = Field(
        default=4000,
        description="Soft trim max chars per message block"
    )
    CONTEXT_SOFT_TRIM_HEAD_RATIO: float = Field(
        default=0.7,
        description="Head ratio for soft trim"
    )
    CONTEXT_SOFT_TRIM_TAIL_RATIO: float = Field(
        default=0.2,
        description="Tail ratio for soft trim"
    )
    CONTEXT_PRUNE_ENABLED: bool = Field(
        default=True,
        description="Enable TTL pruning for old system/tool messages"
    )
    CONTEXT_PRUNE_TTL_SECONDS: int = Field(
        default=300,
        description="TTL (seconds) for prunable system/tool messages"
    )
    CONTEXT_PRUNE_HARD_CLEAR_MIN_CHARS: int = Field(
        default=50000,
        description="Hard-clear prunable messages longer than this"
    )
    CONTEXT_PRUNE_HARD_CLEAR_PLACEHOLDER: str = Field(
        default="[Old tool result content cleared]",
        description="Placeholder for hard-cleared prunable messages"
    )
    STREAM_TOOL_OUTPUT_ENABLED: bool = Field(
        default=False,
        description="Emit full tool output chunks in stream"
    )
    STREAM_TOOL_OUTPUT_MAX_CHARS: int = Field(
        default=4000,
        description="Max characters to emit for tool output chunks"
    )
    STREAM_DEDUPLICATE_CHUNKS: bool = Field(
        default=True,
        description="Suppress duplicate streamed text chunks"
    )
    PROMPT_MINIMAL_ON_SHORT: bool = Field(
        default=False,
        description="Use minimal prompt mode for short messages"
    )
    PROMPT_MINIMAL_MAX_CHARS: int = Field(
        default=20,
        description="Max chars to trigger minimal prompt mode"
    )
    SUBAGENT_ANNOUNCE_ENABLED: bool = Field(
        default=True,
        description="Announce subagent results back to main session"
    )
    SUBAGENT_CLEANUP_AFTER_MINUTES: int = Field(
        default=60,
        description="Minutes before cleanup for subagent sessions (0 = no auto cleanup)"
    )
    SUBAGENT_ANNOUNCE_PUSH_ENABLED: bool = Field(
        default=True,
        description="Push subagent announcements over SSE when available"
    )
    SUBAGENT_ANNOUNCE_PUSH_WINDOW_SECONDS: int = Field(
        default=60,
        description="Seconds to keep SSE open for background subagent announcements"
    )
    SUBAGENT_ANNOUNCE_PUSH_KEEPALIVE_SECONDS: int = Field(
        default=10,
        description="Keepalive interval for SSE while waiting on subagent announcements"
    )
    LOG_LEVEL: str = Field(
        default="INFO",
        description="Logging level: DEBUG, INFO, WARNING, ERROR"
    )
    CORS_ORIGINS: List[str] = Field(
        default=["http://localhost:3000", "http://localhost:5173"],
        description="Allowed CORS origins"
    )
    
    # -------------------------------------------------------------------------
    # Optional Integrations
    # -------------------------------------------------------------------------
    NOTION_API_KEY: Optional[str] = None
    GOOGLE_CLIENT_ID: Optional[str] = None
    GOOGLE_CLIENT_SECRET: Optional[str] = None
    PERPLEXITY_API_KEY: Optional[str] = Field(
        default=None,
        description="Perplexity API key for deep research (Sonar model)"
    )
    TAVILY_API_KEY: Optional[str] = Field(
        default=None,
        description="Tavily API key for fast web search (~0.5s)"
    )
    DEEPGRAM_API_KEY: Optional[str] = Field(
        default=None,
        description="Deepgram API key for voice transcription"
    )
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"  # Ignore extra env vars


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


# Export singleton for convenience
settings = get_settings()
