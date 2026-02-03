# BrainMap Backend (Claude SDK)
#
# A production-grade AI assistant backend built with the Claude Agent SDK.
# Based on battle-tested patterns from Clawdbot.
#
# Structure:
#   core/       - Agent runner, context manager, session management
#   tools/      - Tool definitions and registry
#   skills/     - SKILL.md loader and skill management
#   routers/    - FastAPI endpoints
#   database/   - Supabase client and operations
#   memory/     - File-based memory and vector search
#   scheduler/  - Heartbeat, cron, and wake events
#   tests/      - Unit and integration tests

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging

from core.config import settings

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
logger = logging.getLogger("brainmap")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle manager."""
    # =========================================================================
    # STARTUP
    # =========================================================================
    logger.info("🚀 Starting BrainMap Backend (Claude SDK)")
    
    # Initialize database client
    from database.client import get_client
    supabase = get_client()
    logger.info("✅ Database connected")
    
    # Initialize memory manager
    from memory.manager import get_memory_manager
    memory = get_memory_manager(supabase_client=supabase)
    logger.info("✅ Memory manager initialized")
    
    # Initialize session manager
    from core.session import get_session_manager
    sessions = get_session_manager(supabase, memory)
    logger.info("✅ Session manager initialized")
    
    # Initialize tool registry with core tools
    from tools.registry import get_tool_registry
    from tools.core import set_dependencies as set_tool_deps
    from tools.memory_tools import set_memory_dependencies
    import tools.subagents  # Register subagent tools
    from pathlib import Path

    # Set dependencies for note tools
    set_tool_deps(supabase_client=supabase, memory_manager=memory)

    # Set dependencies for memory tools (separate from notes!)
    set_memory_dependencies(
        memory_manager=memory,
        memory_dir=Path(settings.MEMORY_DIR)
    )

    tool_registry = get_tool_registry()
    logger.info(f"✅ Tool registry initialized ({len(tool_registry._tools)} tools)")
    
    # Log web search providers
    search_providers = []
    if settings.TAVILY_API_KEY:
        search_providers.append("Tavily (fast)")
    if settings.PERPLEXITY_API_KEY:
        search_providers.append("Perplexity (research)")

    if search_providers:
        logger.info(f"🔍 Web search enabled: {', '.join(search_providers)}")
    else:
        logger.info("⚠️ Web search disabled (no TAVILY_API_KEY or PERPLEXITY_API_KEY)")
    
    # Load skills
    from skills.loader import get_skill_loader
    skill_loader = get_skill_loader("./skills")
    skill_count = await skill_loader.load_all()
    logger.info(f"✅ Skills loaded ({skill_count} skills)")
    
    # Initialize prompt builder
    from core.prompt import get_prompt_builder
    prompt_builder = get_prompt_builder(memory, skill_loader)
    logger.info("✅ Prompt builder initialized")
    
    # Initialize agent runner
    from core.agent import get_agent_runner
    agent = get_agent_runner(sessions, tool_registry, prompt_builder)
    logger.info("✅ Agent runner initialized")

    # Wire subagent dependencies
    from core.subagents import set_subagent_dependencies
    set_subagent_dependencies(agent, sessions, prompt_builder)
    
    # Wire up routers
    from routers.chat import set_dependencies as set_chat_deps
    set_chat_deps(agent, sessions)
    logger.info("✅ Chat router wired")
    
    # Wire up voice router (Deepgram STT + OpenAI TTS)
    from routers.voice_stream import set_dependencies as set_voice_deps
    set_voice_deps(agent, sessions)
    logger.info("🎤 Voice router wired (Deepgram STT + TTS)")

    # Wire up diagnostics router
    from routers.diagnostics import set_dependencies as set_diag_deps
    set_diag_deps(prompt_builder, skill_loader)
    logger.info("🧭 Diagnostics router wired")
    
    # Start heartbeat scheduler
    if settings.HEARTBEAT_ENABLED:
        from scheduler.heartbeat import start_heartbeat
        await start_heartbeat(supabase)
        logger.info(f"💓 Heartbeat started (every {settings.HEARTBEAT_INTERVAL_MINUTES} min)")
    
    logger.info("=" * 50)
    logger.info("🧠 BrainMap Backend Ready!")
    logger.info(f"   Model: {settings.CLAUDE_MODEL}")
    logger.info(f"   Context: {settings.MAX_CONTEXT_TOKENS} tokens")
    logger.info(f"   Memory: {settings.MEMORY_DIR}")
    logger.info("=" * 50)
    
    yield
    
    # =========================================================================
    # SHUTDOWN
    # =========================================================================
    logger.info("👋 Shutting down BrainMap Backend")
    
    if settings.HEARTBEAT_ENABLED:
        from scheduler.heartbeat import stop_heartbeat
        await stop_heartbeat()
        logger.info("💓 Heartbeat stopped")


# Create FastAPI app
app = FastAPI(
    title="BrainMap Backend (Claude SDK)",
    description="""
    🧠 **BrainMap AI Assistant Backend**
    
    A production-grade personal AI assistant built with Claude Agent SDK.
    
    ## Features
    - **Agentic Chat**: Context-aware AI with tool calling
    - **Notes System**: Smart saving with deduplication
    - **Reminders**: Natural language scheduling
    - **Memory**: Persistent context across sessions
    - **Proactive**: Heartbeat checks for upcoming events
    
    ## Architecture
    Based on Clawdbot patterns:
    - Session management with compaction
    - Context window tracking (150K tokens)
    - Skill system for extensibility
    - Error resilience with retry/failover
    """,
    version="2.0.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate limiting middleware (from Clawdbot patterns)
from core.rate_limit import RateLimitMiddleware
app.add_middleware(RateLimitMiddleware, user_header="X-User-ID")

# Import and include routers
from routers import health, chat, notes, reminders, diagnostics
from routers import voice_stream
from routers.stubs import (
    today_router, search_router, memories_router,
    notifications_router, apps_router, integrations_router
)

app.include_router(health.router, tags=["Health"])
app.include_router(chat.router, prefix="/v1/chat", tags=["Chat"])
app.include_router(notes.router, prefix="/v1/notes", tags=["Notes"])
app.include_router(reminders.router, prefix="/v1/reminders", tags=["Reminders"])
app.include_router(voice_stream.router, tags=["Voice"])
app.include_router(diagnostics.router)

# Stub routers for app compatibility
app.include_router(today_router)
app.include_router(search_router)
app.include_router(memories_router)
app.include_router(notifications_router)
app.include_router(apps_router)
app.include_router(integrations_router)


@app.get("/")
async def root():
    """Root endpoint with API overview."""
    return {
        "name": "BrainMap Backend (Claude SDK)",
        "version": "2.0.0",
        "status": "running",
        "model": settings.CLAUDE_MODEL,
        "docs": "/docs",
        "endpoints": {
            "chat": "/v1/chat/send",
            "notes": "/v1/notes",
            "reminders": "/v1/reminders",
            "health": "/health"
        }
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8001,  # Different port from old backend
        reload=True
    )
