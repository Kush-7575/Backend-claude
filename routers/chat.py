"""
Chat Router - Agentic Chat with Streaming

The main chat endpoint with:
1. SSE streaming responses
2. Tool execution status updates
3. Session management
4. Context-aware responses
5. Inbound message deduplication (from Clawdbot)
6. Lane queueing for race condition prevention (from Clawdbot)
"""
import logging
from typing import Optional, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from core.dedupe import is_duplicate_inbound
from core.lanes import session_lane
from core.event_bus import get_event_bus
from core.config import settings

logger = logging.getLogger("brainmap.routers.chat")

router = APIRouter()


# =============================================================================
# Request/Response Models
# =============================================================================

class SendMessageRequest(BaseModel):
    """Request to send a message."""
    message: str = Field(..., min_length=1, description="User message")
    session_id: Optional[str] = Field(None, description="Session ID (creates new if not provided)")


class ChatSessionResponse(BaseModel):
    """Chat session info."""
    id: str
    title: Optional[str]
    created_at: str
    message_count: int


# =============================================================================
# Dependencies (to be injected at startup)
# =============================================================================

_agent_runner = None
_session_manager = None


def set_dependencies(agent_runner=None, session_manager=None):
    """Set dependencies for chat router."""
    global _agent_runner, _session_manager
    _agent_runner = agent_runner
    _session_manager = session_manager


def get_agent_runner():
    """Get agent runner."""
    if _agent_runner is None:
        raise HTTPException(status_code=500, detail="Agent not initialized")
    return _agent_runner


def get_session_manager():
    """Get session manager."""
    if _session_manager is None:
        raise HTTPException(status_code=500, detail="Session manager not initialized")
    return _session_manager


# =============================================================================
# Endpoints
# =============================================================================

@router.post("/send")
async def send_message(request: SendMessageRequest):
    """
    Send a message to the AI assistant with streaming response.

    Returns Server-Sent Events (SSE):
    - `data: <token>` - Response text tokens
    - `think: <status>` - Tool execution status
    - `action: <json>` - Structured action data
    - `data: [DONE]` - Stream complete

    Example:
    ```
    POST /v1/chat/send
    {"message": "Save a note about project ideas"}

    Response (SSE):
    think: Saving note...
    data: ✅ Saved:
    data:  Project Ideas
    data: [DONE]
    ```
    """
    agent = get_agent_runner()
    sessions = get_session_manager()

    # Get or create session
    user_id = "current_user"  # TODO: Get from auth

    if request.session_id:
        try:
            session = await sessions.load_session(request.session_id, user_id)
            if not session:
                session = sessions.create_session(user_id)
        except Exception:
            session = sessions.create_session(user_id)
    else:
        session = sessions.create_session(user_id)

    # Inbound dedupe (prevents double-processing retries)
    if is_duplicate_inbound(
        user_id=user_id,
        message=request.message,
        session_id=session.id,
        channel="api",
    ):
        async def duplicate_response():
            yield "data: ⚠️ Duplicate message ignored\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            duplicate_response(),
            media_type="text/event-stream"
        )
    
    # Stream response
    async def generate():
        import json
        import traceback
        import asyncio

        # Issue 2 Fix: Send session_id at start of stream so Fable can track it
        yield f"session: {session.id}\n\n"

        logger.info(f"Starting agent stream for message: {request.message[:50]}...")
        event_bus = get_event_bus()
        event_queue = event_bus.subscribe(session.id)

        try:
            chunk_count = 0
            async for chunk in agent.run_stream(session, request.message):
                chunk_count += 1
                if chunk.type == "text":
                    yield f"data: {chunk.content}\n\n"
                elif chunk.type == "tool_start":
                    yield f"think: Using {chunk.content}...\n\n"
                elif chunk.type == "tool_end":
                    if chunk.metadata:
                        yield f"action: {json.dumps(chunk.metadata)}\n\n"
                elif chunk.type == "lifecycle":
                    payload = {"phase": chunk.content}
                    if chunk.metadata:
                        payload.update(chunk.metadata)
                    yield f"event: lifecycle\ndata: {json.dumps(payload)}\n\n"
                elif chunk.type == "error":
                    yield f"data: ⚠️ {chunk.content}\n\n"
                elif chunk.type == "done":
                    yield "data: [DONE]\n\n"

            logger.info(f"Agent stream completed with {chunk_count} chunks")

            # Save session after response
            await sessions.save_session(session)

            # Keep SSE open briefly for background subagent announcements
            if settings.SUBAGENT_ANNOUNCE_PUSH_ENABLED:
                window = max(0, int(settings.SUBAGENT_ANNOUNCE_PUSH_WINDOW_SECONDS))
                keepalive = max(1, int(settings.SUBAGENT_ANNOUNCE_PUSH_KEEPALIVE_SECONDS))
                deadline = asyncio.get_event_loop().time() + window
                while asyncio.get_event_loop().time() < deadline:
                    timeout = min(keepalive, max(0, deadline - asyncio.get_event_loop().time()))
                    try:
                        event = await asyncio.wait_for(event_queue.get(), timeout=timeout)
                    except asyncio.TimeoutError:
                        # SSE comment keepalive
                        yield ": keepalive\n\n"
                        continue
                    if isinstance(event, dict) and event.get("type") == "subagent_announce":
                        text = event.get("text", "").strip()
                        if text:
                            yield f"data: {text}\n\n"

        except Exception as e:
            logger.error(f"Chat error: {e}")
            logger.error(traceback.format_exc())
            yield f"data: Sorry, an error occurred. Please try again.\n\n"
            yield "data: [DONE]\n\n"
        finally:
            event_bus.unsubscribe(session.id, event_queue)
    
    return StreamingResponse(
        generate(),
        media_type="text/event-stream"
    )


@router.get("/sessions")
async def list_sessions(limit: int = 20):
    """Get recent chat sessions."""
    sessions = get_session_manager()
    user_id = "current_user"
    items = await sessions.list_sessions(user_id=user_id, limit=limit)
    return {"sessions": items, "limit": limit}


@router.get("/sessions/summary")
async def list_sessions_summary(limit: int = 20):
    """Get lightweight session summaries."""
    sessions = get_session_manager()
    user_id = "current_user"
    items = await sessions.list_sessions_summary(user_id=user_id, limit=limit)
    return {"sessions": items, "limit": limit}


@router.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """Get a specific session with messages."""
    sessions = get_session_manager()
    user_id = "current_user"
    
    session = await sessions.load_session(session_id, user_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    return {
        "id": session.id,
        "title": session.title,
        "messages": [m.to_storage() for m in session.messages],
        "created_at": session.created_at.isoformat(),
        "updated_at": session.updated_at.isoformat()
    }


@router.get("/stream")
async def stream_updates(session_id: str):
    """
    Always-on SSE stream for background updates (subagent announcements).
    """
    sessions = get_session_manager()
    user_id = "current_user"
    session = await sessions.load_session(session_id, user_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    async def generate():
        import json
        import asyncio

        yield f"session: {session.id}\n\n"
        event_bus = get_event_bus()
        event_queue = event_bus.subscribe(session.id)
        keepalive = max(1, int(settings.SUBAGENT_ANNOUNCE_PUSH_KEEPALIVE_SECONDS))
        try:
            while True:
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=keepalive)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if isinstance(event, dict) and event.get("type") == "subagent_announce":
                    payload = {
                        "type": "subagent_announce",
                        "text": event.get("text", ""),
                        "run_id": event.get("run_id"),
                    }
                    yield f"event: subagent\ndata: {json.dumps(payload)}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            event_bus.unsubscribe(session.id, event_queue)

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.get("/sessions/{session_id}/status")
async def get_session_status(session_id: str):
    """Get session status summary (tokens, compactions, counts)."""
    sessions = get_session_manager()
    user_id = "current_user"
    session = await sessions.load_session(session_id, user_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return await sessions.get_session_status(session)


@router.get("/sessions/search")
async def search_sessions(q: str, limit: int = 5):
    """Search past sessions for relevant content."""
    sessions = get_session_manager()
    user_id = "current_user"
    results = await sessions.search_session_history(q, user_id=user_id, limit=limit)
    return {"query": q, "results": results, "limit": limit}


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(session_id: str):
    """Delete a chat session."""
    sessions = get_session_manager()
    user_id = "current_user"
    ok = await sessions.delete_session(session_id, user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Session not found")


@router.get("/greeting")
async def get_greeting():
    """Get a greeting message for the chat."""
    from datetime import datetime
    hour = datetime.now().hour
    
    if hour < 12:
        greeting = "Good morning! What's on your mind?"
    elif hour < 17:
        greeting = "Good afternoon! How can I help?"
    else:
        greeting = "Good evening! What can I do for you?"
    
    return {"greeting": greeting}


@router.post("/sessions")
async def create_session(title: str = None):
    """Create a new chat session."""
    sessions = get_session_manager()
    user_id = "current_user"  # TODO: Get from auth
    
    session = sessions.create_session(user_id)
    if title:
        session.title = title
    
    return {
        "id": session.id,
        "title": session.title,
        "created_at": session.created_at.isoformat()
    }
