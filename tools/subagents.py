"""
Subagent tools - spawn background subagent runs.
"""
from typing import Optional, Dict, Any

from tools.registry import get_tool_registry
from core.subagents import spawn_subagent

registry = get_tool_registry()


@registry.register(
    name="sessions_spawn",
    description=(
        "Spawn a background subagent run in an isolated session and report the result "
        "back to the current session."
    ),
)
async def sessions_spawn(
    task: str,
    label: Optional[str] = None,
    cleanup: Optional[str] = "keep",
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Spawn a subagent to handle a specific task.

    Args:
        task: Task description for the subagent.
        label: Optional label for the subagent run.
        cleanup: "keep" or "delete" subagent session after completion.
    """
    context = context or {}
    session_id = context.get("session_id")
    user_id = context.get("user_id") or "current_user"

    if not session_id:
        return {"status": "error", "error": "Missing session_id for subagent spawn"}

    cleanup_mode = cleanup if cleanup in ("keep", "delete") else "keep"
    return await spawn_subagent(
        parent_session_id=session_id,
        user_id=user_id,
        task=task,
        label=label,
        cleanup=cleanup_mode,
    )
