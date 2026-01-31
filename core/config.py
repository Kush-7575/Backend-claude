"""
BrainMap Core Configuration

Centralized settings using Pydantic Settings for type-safe configuration.
All settings can be overridden via environment variables.
"""
from typing import List, Optional
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
    EMBEDDING_PROVIDER: str = Field(
        default="openai",
        description="Vector embedding provider: openai, local, none"
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
