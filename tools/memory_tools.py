"""
Memory Tools - Separate from Notes

Memory = Auto-captured context (daily logs, long_term_memory, sessions)
Notes = User-explicitly-saved content (notes table)

These tools search/read memory from Supabase tables (persistent on Railway).
Falls back to local files only if database unavailable.
"""
import logging
import time
from collections import OrderedDict
from typing import Optional, List, Dict, Any
from pathlib import Path
from datetime import datetime, timezone

from tools.registry import get_tool_registry
from core.config import settings

logger = logging.getLogger("brainmap.tools.memory")

# Module-level dependencies (injected at startup)
_memory_manager = None
_memory_dir: Optional[Path] = None

# memory_search cache (query -> result)
_memory_search_cache: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()


def set_memory_dependencies(memory_manager=None, memory_dir: Optional[Path] = None):
    """Inject dependencies for memory tools."""
    global _memory_manager, _memory_dir
    _memory_manager = memory_manager
    _memory_dir = memory_dir or Path("./memory_store")


registry = get_tool_registry()


# =============================================================================
# Memory Search Tool - UNIFIED SEARCH (Like Clawdbot)
# =============================================================================

@registry.register(
    name="memory_search",
    description="""Search EVERYTHING about the user - unified memory search.

Searches across ALL sources (stored in Supabase for persistence):
- Long-term memory (long_term_memory table) - Facts and preferences
- Daily logs (daily_logs table) - ALL conversation history
- Session chunks (session_chunks table) - Past conversations indexed for search
- Notes - User-saved content (recipes, lists, preferences)
- Reminders - Tasks and to-dos (pending and completed)

Use this as your PRIMARY search tool for any question about:
- Past conversations ("what did we discuss about X?")
- User preferences ("what's my favorite coffee?")
- Saved content ("what notes do I have about Y?")
- Tasks/reminders ("what do I need to do?")

Returns ranked results from all sources."""
)
async def memory_search(
    query: str,
    max_results: int = 10,
    min_score: float = 0.2,
    sources: Optional[List[str]] = None,
    use_hybrid: bool = True,
    vector_weight: Optional[float] = None,
    text_weight: Optional[float] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Unified search across ALL user data - like Clawdbot's memory_search.

    Searches: memory files, daily logs, sessions, notes, AND reminders.
    This gives the agent a complete view of "everything about the user".
    """
    if not _memory_manager:
        return {
            "success": False,
            "error": "Memory manager not configured"
        }

    sources = sources or ["memory", "daily", "sessions", "notes", "reminders"]
    all_results = []

    try:
        cache_key = _build_memory_cache_key(
            query=query,
            max_results=max_results,
            min_score=min_score,
            sources=sources,
            use_hybrid=use_hybrid,
            vector_weight=vector_weight,
            text_weight=text_weight
        )
        cached = _memory_cache_get(cache_key)
        if cached:
            return cached

        # 1. Search memory sources (memory files, daily logs, sessions)
        memory_sources = [s for s in sources if s in ["memory", "daily", "sessions"]]
        if memory_sources:
            memory_results = await _memory_manager.search(
                query=query,
                limit=max_results,
                sources=memory_sources,
                use_hybrid=use_hybrid,
                vector_weight=vector_weight,
                text_weight=text_weight
            )
            all_results.extend(memory_results)

        # 2. Search notes if included
        if "notes" in sources:
            note_results = await _search_notes_for_memory(
                query,
                max_results,
                vector_weight=vector_weight,
                text_weight=text_weight
            )
            all_results.extend(note_results)

        # 3. Search reminders if included
        if "reminders" in sources:
            reminder_results = await _search_reminders_for_memory(query, max_results)
            all_results.extend(reminder_results)

        # Apply role-aware source weighting
        all_results = _apply_source_weights(all_results, query)

        # Filter by min_score and sort by relevance
        all_results = [r for r in all_results if r.get("relevance", 0) >= min_score]
        all_results.sort(key=lambda x: x.get("relevance", 0), reverse=True)
        all_results = all_results[:max_results]

        if not all_results:
            return {
                "success": True,
                "results": [],
                "message": f"No memories found for '{query}'"
            }

        # Format results for the agent
        formatted = []
        for r in all_results:
            formatted.append({
                "content": r.get("content", r.get("text", "")),
                "source": r.get("source", "unknown"),
                "score": round(r.get("relevance", r.get("score", 0)), 3),
                "title": r.get("title", ""),
                "path": r.get("path", r.get("file", "")),
                "date": r.get("date", r.get("timestamp", ""))
            })

        response = {
            "success": True,
            "results": formatted,
            "count": len(formatted),
            "query": query,
            "sources_searched": sources
        }
        _memory_cache_put(cache_key, response)
        return response

    except Exception as e:
        logger.error(f"memory_search error: {e}")
        return {
            "success": False,
            "error": str(e)
        }


def _apply_source_weights(results: List[Dict[str, Any]], query: str) -> List[Dict[str, Any]]:
    """Apply source-specific weights based on query intent."""
    if not results:
        return results
    text = (query or "").lower()

    prefers_preferences = any(
        kw in text for kw in ["favorite", "preference", "prefer", "like", "dislike", "my "]
    )
    prefers_history = any(
        kw in text for kw in ["last time", "earlier", "previous", "we discussed", "you said", "what did we"]
    )

    base_weights = {
        "long_term_memory": 1.2,
        "daily_log": 1.0,
        "sessions": 1.0,
        "notes": 0.95,
        "notes_hybrid": 0.95,
        "note": 0.95,
        "reminders": 1.0,
    }

    if prefers_preferences:
        base_weights["long_term_memory"] = 1.4
    if prefers_history:
        base_weights["daily_log"] = 1.2
        base_weights["sessions"] = 1.2

    for r in results:
        source = r.get("source", "unknown")
        weight = base_weights.get(source, 1.0)
        base_score = r.get("relevance", r.get("score", 0)) or 0
        r["relevance"] = base_score * weight

    return results


def _build_memory_cache_key(
    query: str,
    max_results: int,
    min_score: float,
    sources: List[str],
    use_hybrid: bool,
    vector_weight: Optional[float],
    text_weight: Optional[float]
) -> str:
    sources_key = ",".join(sorted(sources))
    return "|".join([
        query.strip().lower(),
        f"max={max_results}",
        f"min={min_score}",
        f"sources={sources_key}",
        f"hybrid={use_hybrid}",
        f"vw={vector_weight if vector_weight is not None else 'default'}",
        f"tw={text_weight if text_weight is not None else 'default'}",
    ])


def _memory_cache_get(key: str) -> Optional[Dict[str, Any]]:
    if not settings.MEMORY_SEARCH_CACHE_ENABLED:
        return None
    entry = _memory_search_cache.get(key)
    if not entry:
        return None
    expires_at = entry.get("_expires_at", 0)
    if expires_at and time.time() > expires_at:
        _memory_search_cache.pop(key, None)
        return None
    # Refresh LRU order
    _memory_search_cache.move_to_end(key)
    return entry.get("value")


def _memory_cache_put(key: str, value: Dict[str, Any]) -> None:
    if not settings.MEMORY_SEARCH_CACHE_ENABLED:
        return
    ttl = max(1, int(settings.MEMORY_SEARCH_CACHE_TTL_SECONDS))
    _memory_search_cache[key] = {
        "value": value,
        "_expires_at": time.time() + ttl
    }
    _memory_search_cache.move_to_end(key)
    max_entries = max(1, int(settings.MEMORY_SEARCH_CACHE_MAX_ENTRIES))
    while len(_memory_search_cache) > max_entries:
        _memory_search_cache.popitem(last=False)


# =============================================================================
# Helper Functions for Unified Search
# =============================================================================

async def _search_notes_for_memory(
    query: str,
    limit: int,
    vector_weight: Optional[float] = None,
    text_weight: Optional[float] = None
) -> List[Dict[str, Any]]:
    """Search notes and format for unified memory results."""
    results = []

    try:
        # Import here to avoid circular imports
        from database.vector_db import search_notes_hybrid

        notes = search_notes_hybrid(
            query=query,
            user_id=None,  # Search all
            limit=limit,
            min_score=0.2,
            vector_weight=vector_weight,
            text_weight=text_weight
        )

        for note in notes:
            results.append({
                "source": "note",
                "content": f"{note.get('title', 'Untitled')}: {note.get('content', '')[:300]}",
                "title": note.get("title", ""),
                "relevance": note.get("score", 0.5),
                "id": note.get("id"),
                "type": note.get("type", "note")
            })

    except Exception as e:
        logger.warning(f"Note search for memory failed: {e}")

    return results


async def _search_reminders_for_memory(query: str, limit: int) -> List[Dict[str, Any]]:
    """Search reminders/tasks and format for unified memory results.

    Note: Reminders and tasks are stored in the `action_items` table.
    There is no separate `reminders` table.
    """
    results = []

    # Get the supabase client from memory manager
    if not _memory_manager or not _memory_manager._supabase:
        return results

    try:
        supabase = _memory_manager._supabase
        query_lower = query.lower()

        # Search action_items table (this is where reminders/tasks are stored)
        response = supabase.table("action_items").select("*").order(
            "created_at", desc=True
        ).limit(limit * 2).execute()

        for item in response.data or []:
            title = item.get("title", "")
            # Simple keyword matching for relevance
            if query_lower in title.lower():
                relevance = 0.7  # Higher relevance for exact match
            elif any(word in title.lower() for word in query_lower.split()):
                relevance = 0.5  # Partial match
            else:
                continue  # Skip non-matching items

            status = item.get("status", "pending")
            due = item.get("due_at", "")

            results.append({
                "source": "reminder",
                "content": f"[{status.upper()}] {title}" + (f" (due: {due})" if due else ""),
                "title": title[:50],
                "relevance": relevance,
                "id": item.get("id"),
                "status": status,
                "due_time": due
            })

    except Exception as e:
        logger.warning(f"Reminder search for memory failed: {e}")

    return results[:limit]


# =============================================================================
# Memory Get Tool
# =============================================================================

@registry.register(
    name="memory_get",
    description="""Read memory content by type.

Valid types:
- MEMORY.md or long_term - Long-term facts and preferences
- daily/<date> - Daily conversation logs for a specific date (e.g., daily/2024-01-15)

Uses Supabase tables as primary storage (persistent on Railway).
Falls back to local files only if database unavailable.

Use this to read specific memory content.
For searching across all memories, use memory_search instead."""
)
async def memory_get(
    path: str,
    limit: int = 50,
    user_id: str = "default",
    **kwargs
) -> Dict[str, Any]:
    """
    Read memory content from Supabase tables or local files.

    Supports:
    - MEMORY.md / long_term → long_term_memory table
    - daily/YYYY-MM-DD → daily_logs table for that date
    """
    if not _memory_manager:
        return {
            "success": False,
            "error": "Memory manager not configured"
        }

    path = path.strip().lstrip("/\\")
    limit = min(max(1, limit), 200)

    try:
        # Handle long-term memory request
        if path.lower() in ["memory.md", "long_term", "long-term"]:
            content = await _memory_manager.read_long_term_memory(user_id=user_id)
            if content:
                lines = content.split("\n")[:limit]
                return {
                    "success": True,
                    "path": "long_term_memory",
                    "content": "\n".join(lines),
                    "entry_count": len(lines),
                    "source": "database" if _memory_manager._use_database else "file"
                }
            else:
                return {
                    "success": True,
                    "path": "long_term_memory",
                    "content": "",
                    "message": "No long-term memories found"
                }

        # Handle daily log request
        if path.lower().startswith("daily/"):
            date_str = path[6:]  # Remove "daily/"
            try:
                target_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                return {
                    "success": False,
                    "error": f"Invalid date format: {date_str}. Use YYYY-MM-DD"
                }

            content = await _memory_manager.read_daily_log(date=target_date, user_id=user_id)
            if content:
                return {
                    "success": True,
                    "path": f"daily/{date_str}",
                    "content": content[:10000],  # Limit content size
                    "date": date_str,
                    "source": "database" if _memory_manager._use_database else "file"
                }
            else:
                return {
                    "success": True,
                    "path": f"daily/{date_str}",
                    "content": "",
                    "message": f"No daily log entries for {date_str}"
                }

        return {
            "success": False,
            "error": f"Unknown path: {path}. Use 'MEMORY.md', 'long_term', or 'daily/YYYY-MM-DD'"
        }

    except Exception as e:
        logger.error(f"memory_get error: {e}")
        return {
            "success": False,
            "error": str(e)
        }


# =============================================================================
# Memory List Tool
# =============================================================================

@registry.register(
    name="memory_list",
    description="""List available memory content.

Shows what memory exists:
- long_term_memory - Long-term facts and preferences
- daily_logs - Daily conversation logs (recent dates)

Uses Supabase tables as primary storage (persistent on Railway).

Use this to discover what memory is available before reading it."""
)
async def memory_list(
    category: str = "all",
    user_id: str = "default",
    **kwargs
) -> Dict[str, Any]:
    """List available memory from database or files."""
    if not _memory_manager:
        return {
            "success": False,
            "error": "Memory manager not configured"
        }

    try:
        items = []

        # List long-term memories
        if category in ["all", "memory"]:
            if _memory_manager._use_database and _memory_manager._supabase:
                try:
                    response = _memory_manager._supabase.table("long_term_memory").select(
                        "id, category, importance, created_at"
                    ).eq("user_id", user_id).order("created_at", desc=True).limit(50).execute()

                    # Group by category
                    categories = {}
                    for mem in response.data or []:
                        cat = mem.get("category", "general")
                        categories[cat] = categories.get(cat, 0) + 1

                    for cat, count in categories.items():
                        items.append({
                            "type": "long_term_memory",
                            "category": cat,
                            "count": count,
                            "source": "database"
                        })
                except Exception as e:
                    logger.warning(f"Failed to list long_term_memory: {e}")
            else:
                # Fallback to file
                memory_md = _memory_dir / "MEMORY.md"
                if memory_md.exists():
                    stat = memory_md.stat()
                    items.append({
                        "type": "long_term_memory",
                        "path": "MEMORY.md",
                        "size": stat.st_size,
                        "source": "file"
                    })

        # List daily logs
        if category in ["all", "daily"]:
            if _memory_manager._use_database and _memory_manager._supabase:
                try:
                    # Get distinct dates with entry counts
                    response = _memory_manager._supabase.table("daily_logs").select(
                        "log_date"
                    ).eq("user_id", user_id).order("log_date", desc=True).limit(30).execute()

                    # Count entries per date
                    date_counts = {}
                    for entry in response.data or []:
                        date = entry.get("log_date", "")
                        date_counts[date] = date_counts.get(date, 0) + 1

                    for date, count in date_counts.items():
                        items.append({
                            "type": "daily_log",
                            "date": date,
                            "entry_count": count,
                            "source": "database"
                        })
                except Exception as e:
                    logger.warning(f"Failed to list daily_logs: {e}")
            else:
                # Fallback to files
                daily_dir = _memory_dir / "daily"
                if daily_dir.exists():
                    daily_files = sorted(daily_dir.glob("*.md"), reverse=True)[:30]
                    for f in daily_files:
                        stat = f.stat()
                        items.append({
                            "type": "daily_log",
                            "date": f.stem,
                            "size": stat.st_size,
                            "source": "file"
                        })

        return {
            "success": True,
            "items": items,
            "count": len(items),
            "storage": "database" if _memory_manager._use_database else "file"
        }

    except Exception as e:
        logger.error(f"memory_list error: {e}")
        return {
            "success": False,
            "error": str(e)
        }


# =============================================================================
# Export all tools
# =============================================================================

MEMORY_TOOLS = [
    "memory_search",
    "memory_get",
    "memory_list"
]


def get_memory_tools() -> List[str]:
    """Return list of memory tool names."""
    return MEMORY_TOOLS
