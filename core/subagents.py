"""
Subagent orchestration - background task runner (Clawdbot-style).

Creates isolated child sessions to do focused work, then reports results
back to the parent session as a system message.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from uuid import uuid4

from core.config import settings
from core.event_bus import get_event_bus

logger = logging.getLogger("brainmap.subagents")


SUBAGENT_RULES = """# Subagent Context

You are a subagent spawned by the main agent for a specific task.

## Your Role
- You were created to handle: {task}
- Complete this task. That's your entire purpose.
- You are NOT the main agent. Don't try to be.

## Rules
1. Stay focused: do the assigned task only.
2. Complete the task and return the result.
3. No user chat. No proactive actions.
4. Do NOT use any messaging tools to contact users.
5. Be concise and informative.
"""


@dataclass
class SubagentRun:
    run_id: str
    child_session_id: str
    parent_session_id: str
    user_id: str
    task: str
    label: Optional[str] = None
    cleanup: str = "keep"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    status: str = "queued"  # queued|running|ok|error
    result: Optional[str] = None
    error: Optional[str] = None
    announce_status: Optional[str] = None


class SubagentRegistry:
    """In-memory registry for subagent runs."""

    def __init__(self) -> None:
        self._runs: Dict[str, SubagentRun] = {}

    def register(self, run: SubagentRun) -> None:
        self._runs[run.run_id] = run

    def get(self, run_id: str) -> Optional[SubagentRun]:
        return self._runs.get(run_id)


_registry = SubagentRegistry()
_agent_runner = None
_session_manager = None
_prompt_builder = None


def set_subagent_dependencies(agent_runner=None, session_manager=None, prompt_builder=None) -> None:
    global _agent_runner, _session_manager, _prompt_builder
    _agent_runner = agent_runner
    _session_manager = session_manager
    _prompt_builder = prompt_builder


def _require_dependencies():
    if _agent_runner is None or _session_manager is None or _prompt_builder is None:
        raise RuntimeError("Subagent dependencies not initialized")
    return _agent_runner, _session_manager, _prompt_builder


def _build_subagent_prompt(task: str) -> str:
    return SUBAGENT_RULES.format(task=task.strip())


async def spawn_subagent(
    *,
    parent_session_id: str,
    user_id: str,
    task: str,
    label: Optional[str] = None,
    cleanup: str = "keep",
) -> Dict[str, Any]:
    """Spawn a subagent run in the background and return identifiers."""
    agent_runner, sessions, prompt_builder = _require_dependencies()

    # Prevent subagent spawning from subagent sessions
    parent = await sessions.load_session(parent_session_id, user_id)
    if parent and parent.metadata.get("is_subagent"):
        return {"status": "error", "error": "sessions_spawn not allowed from subagent session"}

    # Create child session
    child = sessions.create_session(user_id=user_id, title=label)
    child.metadata["is_subagent"] = True
    child.metadata["parent_session_id"] = parent_session_id
    child.metadata["label"] = label or ""

    run_id = str(uuid4())
    run = SubagentRun(
        run_id=run_id,
        child_session_id=child.id,
        parent_session_id=parent_session_id,
        user_id=user_id,
        task=task,
        label=label,
        cleanup=cleanup,
    )
    _registry.register(run)

    async def _run():
        try:
            run.started_at = datetime.now(timezone.utc)
            run.status = "running"

            # Build minimal prompt + subagent rules
            base_prompt = await prompt_builder.build(
                user_id=user_id,
                context={"last_message": task, "skip_memory": True},
                mode="minimal",
            )
            system_prompt = "\n\n".join([base_prompt, _build_subagent_prompt(task)]).strip()

            # Run subagent turn
            response = await agent_runner.run(
                session=child,
                user_message=task,
                system_prompt=system_prompt,
                tools=None,
            )
            run.result = response.content or ""
            run.status = "ok"

            # Announce back to parent session (Clawdbot-style: main agent replies)
            parent = await sessions.load_session(parent_session_id, user_id)
            if parent and settings.SUBAGENT_ANNOUNCE_ENABLED:
                summary = run.result or "(no output)"
                trigger_message = "\n".join(
                    [
                        f'A background task "{label or task}" just completed.',
                        "",
                        "Findings:",
                        summary,
                        "",
                        "Summarize this naturally for the user. Keep it brief (1-2 sentences).",
                        "Do not mention tokens, stats, or that this was a background task.",
                        "If nothing user-facing, respond with NO_REPLY.",
                    ]
                )
                announce_response = await agent_runner.run(
                    session=parent,
                    user_message=trigger_message,
                    system_prompt=None,
                    tools=None,
                )
                announce_text = (announce_response.content or "").strip()
                run.announce_status = "ok" if announce_text else "empty"
                if announce_text and announce_text.strip().upper() != "NO_REPLY":
                    await get_event_bus().publish(parent_session_id, {
                        "type": "subagent_announce",
                        "text": announce_text,
                        "run_id": run_id,
                    })
            elif parent:
                # Fallback: store raw findings if announce disabled
                summary = run.result or "(no output)"
                payload = "\n".join(
                    [
                        "Subagent result:",
                        f"Task: {task}",
                        "Findings:",
                        summary,
                    ]
                )
                await sessions.add_message(
                    parent,
                    "system",
                    payload,
                    metadata={"type": "subagent_result", "run_id": run_id},
                )
                run.announce_status = "stored"

            if cleanup == "delete":
                await sessions.delete_session(child.id, user_id)

        except Exception as e:
            run.error = str(e)
            run.status = "error"
            logger.error(f"Subagent run failed: {e}")
        finally:
            run.ended_at = datetime.now(timezone.utc)
            # Optional delayed cleanup
            cleanup_minutes = max(0, int(settings.SUBAGENT_CLEANUP_AFTER_MINUTES))
            if cleanup_minutes > 0:
                async def _delayed_cleanup():
                    await asyncio.sleep(cleanup_minutes * 60)
                    try:
                        await sessions.delete_session(child.id, user_id)
                    except Exception:
                        pass
                asyncio.create_task(_delayed_cleanup())

    asyncio.create_task(_run())

    return {
        "status": "started",
        "run_id": run_id,
        "child_session_id": child.id,
    }
