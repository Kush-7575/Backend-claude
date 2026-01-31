"""
Diagnostics Router - prompt, tools, and skills visibility.
"""
import logging
from typing import Optional, Dict, Any

from fastapi import APIRouter, HTTPException

from tools.registry import get_tool_registry
from tools.policy import get_tool_policy

logger = logging.getLogger("brainmap.routers.diagnostics")

router = APIRouter(prefix="/v1/diagnostics", tags=["Diagnostics"])

_prompt_builder = None
_skill_loader = None


def set_dependencies(prompt_builder=None, skill_loader=None):
    """Inject dependencies from app startup."""
    global _prompt_builder, _skill_loader
    _prompt_builder = prompt_builder
    _skill_loader = skill_loader


def _require_prompt_builder():
    if _prompt_builder is None:
        raise HTTPException(status_code=500, detail="Prompt builder not initialized")
    return _prompt_builder


@router.get("/tools")
async def list_tools() -> Dict[str, Any]:
    """List tools currently available to the default user."""
    registry = get_tool_registry()
    policy = get_tool_policy()
    user_id = "current_user"
    allowed = set(policy.resolve(user_id))
    summaries = registry.get_tool_summaries(allowlist=allowed)
    tools = [{"name": name, "description": summaries.get(name, "")} for name in sorted(allowed)]
    return {"tools": tools, "count": len(tools)}


@router.get("/skills")
async def list_skills() -> Dict[str, Any]:
    """List skills currently available for prompt injection."""
    if _skill_loader is None:
        raise HTTPException(status_code=500, detail="Skill loader not initialized")
    entries = _skill_loader.list_available_skills(tool_registry=get_tool_registry())
    return {
        "skills": entries,
        "count": len(entries),
        "version": _skill_loader.get_snapshot_version(),
    }


@router.get("/prompt-report")
async def prompt_report() -> Dict[str, Any]:
    """Generate a lightweight prompt report for debugging."""
    builder = _require_prompt_builder()
    user_id = "current_user"
    report = await builder.build_with_report(user_id=user_id, mode="full")
    prompt = report.get("prompt", "")
    skills = []
    if _skill_loader:
        skills = _skill_loader.list_available_skills(tool_registry=get_tool_registry())
    registry = get_tool_registry()
    policy = get_tool_policy()
    allowed = set(policy.resolve(user_id))
    summaries = registry.get_tool_summaries(allowlist=allowed)

    tool_entries = [
        {"name": name, "description_chars": len(summaries.get(name, ""))}
        for name in sorted(allowed)
    ]
    skill_entries = [
        {"name": s["name"], "description_chars": len(s.get("description", ""))}
        for s in skills
    ]

    workspace_files = builder.get_workspace_report()
    workspace_stats = [
        {
            "name": f["name"],
            "path": f["path"],
            "missing": f["missing"],
            "raw_chars": f["raw_chars"],
            "injected_chars": f["injected_chars"],
            "truncated": f["truncated"],
        }
        for f in workspace_files
    ]

    return {
        "prompt_chars": len(prompt),
        "prompt_tokens": report.get("total_tokens", 0),
        "prompt_sections": report.get("sections", []),
        "tools": {
            "count": len(tool_entries),
            "entries": tool_entries,
        },
        "skills": {
            "count": len(skill_entries),
            "entries": skill_entries,
        },
        "workspace": {
            "files": workspace_stats,
            "total_injected_chars": sum(f["injected_chars"] for f in workspace_stats),
        },
    }
