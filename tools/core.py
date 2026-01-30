"""
Core Tools - Notes, Search, Reminders

The essential tools for BrainMap's functionality:
1. smart_save - Intelligent note saving with deduplication
2. search_notes - Semantic search across notes
3. create_reminder - Natural language reminder creation
4. get_notes / get_reminders - List items
5. Utility tools (time, etc.)

These implement the "Smart Save" pattern from the existing BrainMap.
"""
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone, timedelta
from uuid import uuid4
import re

from tools.registry import tool, get_tool_registry

logger = logging.getLogger("brainmap.tools.core")


# =============================================================================
# Dependency Injection
# We'll inject these at startup
# =============================================================================

_supabase = None
_memory_manager = None


def set_dependencies(supabase_client=None, memory_manager=None):
    """Set dependencies for tools."""
    global _supabase, _memory_manager
    _supabase = supabase_client
    _memory_manager = memory_manager


def _get_supabase():
    """Get Supabase client."""
    if _supabase is None:
        raise RuntimeError("Supabase client not initialized")
    return _supabase


# =============================================================================
# Notes Tools
# =============================================================================

@tool(
    name="smart_save",
    description="""Intelligently save content to notes. Automatically:
    - Searches for existing notes with similar topic
    - Appends to existing note if found (avoids duplicates)
    - Creates new note if no match
    
    USE THIS as the primary way to save notes."""
)
async def smart_save(
    topic: str,
    content: str,
    note_type: str = "collection"
) -> Dict[str, Any]:
    """
    Smart save with automatic deduplication.
    
    Args:
        topic: Topic/title for the note
        content: Content to save
        note_type: 'collection' for notes, 'list' for checklists
    
    Returns:
        Dict with note_id, title, status ('appended' or 'created')
    """
    db = _get_supabase()
    
    try:
        # 1. Search for existing notes with similar topic
        existing = await _find_similar_note(topic)
        
        if existing:
            # Append to existing note
            note_id = existing["id"]
            item_id = str(uuid4())
            now = datetime.now(timezone.utc).isoformat()
            
            # Add new item
            result = db.table("note_items").insert({
                "id": item_id,
                "note_id": note_id,
                "user_id": existing["user_id"],
                "content": content,
                "checked": False,
                "added_at": now
            }).execute()
            
            if not result.data:
                logger.error(f"Failed to insert note item for note: {note_id}")
                return {
                    "status": "error",
                    "message": "Failed to save content to note"
                }
            
            # Update note timestamp
            db.table("notes").update({
                "updated_at": now
            }).eq("id", note_id).execute()
            
            # Update embedding for the note
            try:
                from database.vector_db import upsert_note_embedding
                upsert_note_embedding(note_id, existing["title"], content)
            except Exception as e:
                logger.warning(f"Failed to update embedding: {e}")
            
            logger.info(f"Appended to existing note: {existing['title']}")
            
            return {
                "status": "appended",
                "note_id": note_id,
                "title": existing["title"],
                "message": f"Added to existing note: {existing['title']}"
            }
        
        else:
            # Create new note
            note_id = str(uuid4())
            now = datetime.now(timezone.utc).isoformat()
            
            result = db.table("notes").insert({
                "id": note_id,
                "user_id": "current_user",  # TODO: Get from context
                "title": topic,
                "type": note_type,
                "created_at": now,
                "updated_at": now
            }).execute()
            
            if not result.data:
                logger.error(f"Failed to create note: {topic}")
                return {
                    "status": "error",
                    "message": "Failed to create note"
                }
            
            # Add content as first item
            item_result = db.table("note_items").insert({
                "id": str(uuid4()),
                "note_id": note_id,
                "user_id": "current_user",
                "content": content,
                "checked": False,
                "added_at": now
            }).execute()
            
            if not item_result.data:
                logger.error(f"Failed to insert content for note: {topic}")
                # Note was created but item failed - still report partial success
            
            # Create embedding for the new note
            try:
                from database.vector_db import upsert_note_embedding
                upsert_note_embedding(note_id, topic, content)
            except Exception as e:
                logger.warning(f"Failed to create embedding: {e}")
            
            logger.info(f"Created new note: {topic}")
            
            return {
                "status": "created",
                "note_id": note_id,
                "title": topic,
                "message": f"Created new note: {topic}"
            }
            
    except Exception as e:
        logger.error(f"smart_save error: {e}")
        return {
            "status": "error",
            "message": f"Failed to save: {str(e)}"
        }


@tool(
    name="save_note",
    description="""Save content as a new note. Use smart_save instead unless you 
    specifically need to create a NEW note without checking for duplicates."""
)
async def save_note(
    title: str,
    content: str,
    note_type: str = "collection"
) -> Dict[str, Any]:
    """Create a new note directly (bypasses dedup check)."""
    db = _get_supabase()
    
    note_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()
    
    db.table("notes").insert({
        "id": note_id,
        "user_id": "current_user",
        "title": title,
        "type": note_type,
        "created_at": now,
        "updated_at": now
    }).execute()
    
    db.table("note_items").insert({
        "id": str(uuid4()),
        "note_id": note_id,
        "user_id": "current_user",
        "content": content,
        "checked": False,
        "added_at": now
    }).execute()
    
    return {
        "status": "created",
        "note_id": note_id,
        "title": title
    }


@tool(
    name="search_notes",
    description="""Search user's saved notes using hybrid search. Use when:
    - User asks "what did I save about..."
    - User asks "my notes on..."
    - User asks "find my note about..."
    - Before creating a note (to avoid duplicates)

    Uses semantic + keyword search for best results."""
)
async def search_notes(
    query: str,
    limit: int = 5
) -> List[Dict[str, Any]]:
    """
    Search notes using hybrid search (vector + keyword).

    Combines semantic similarity (meaning) and keyword matching (exact terms)
    for better search results. Based on Clawdbot's hybrid.ts patterns.
    """
    try:
        from database.vector_db import search_notes_hybrid

        # Use hybrid search for best results
        results = search_notes_hybrid(
            query=query,
            limit=limit,
            min_score=0.3  # Lower threshold for search, user wants broad results
        )

        notes = []
        for row in results:
            notes.append({
                "id": row.get("id"),
                "title": row.get("title"),
                "type": row.get("type", "collection"),
                "preview": (row.get("content") or "")[:100],
                "score": row.get("score", 0),
                "created_at": row.get("created_at")
            })

        if notes:
            logger.info(f"Hybrid search found {len(notes)} results for: {query}")
            return notes

    except Exception as e:
        logger.warning(f"Hybrid search failed, using fallback: {e}")

    # Fallback to ILIKE search
    db = _get_supabase()

    result = db.table("notes")\
        .select("id, title, type, created_at, note_items(content)")\
        .eq("deleted", False)\
        .ilike("title", f"%{query}%")\
        .limit(limit)\
        .execute()

    notes = []
    for row in result.data:
        items = row.get("note_items", [])
        preview = items[0]["content"][:100] if items else ""

        notes.append({
            "id": row["id"],
            "title": row["title"],
            "type": row.get("type", "collection"),
            "preview": preview,
            "created_at": row.get("created_at")
        })

    return notes


@tool(
    name="get_notes",
    description="""Get user's recent notes. Use when:
    - User asks "show my notes"
    - User asks "what have I saved?"
    - User wants to see their notes list"""
)
async def get_notes(limit: int = 10) -> List[Dict[str, Any]]:
    """Get recent notes."""
    db = _get_supabase()
    
    result = db.table("notes")\
        .select("id, title, type, created_at, updated_at")\
        .eq("deleted", False)\
        .order("updated_at", desc=True)\
        .limit(limit)\
        .execute()
    
    return result.data


@tool(
    name="append_to_note",
    description="""Add content to an existing note. Requires note_id.
    Use search_notes first to find the note."""
)
async def append_to_note(
    note_id: str,
    content: str
) -> Dict[str, Any]:
    """Append content to existing note."""
    db = _get_supabase()
    now = datetime.now(timezone.utc).isoformat()
    
    # Add item
    db.table("note_items").insert({
        "id": str(uuid4()),
        "note_id": note_id,
        "user_id": "current_user",
        "content": content,
        "checked": False,
        "added_at": now
    }).execute()
    
    # Update timestamp
    db.table("notes").update({
        "updated_at": now
    }).eq("id", note_id).execute()
    
    return {"status": "appended", "note_id": note_id}


@tool(
    name="delete_note",
    description="""Delete a note by searching for it. Searches by query,
    confirms before deletion if multiple matches."""
)
async def delete_note(query: str) -> Dict[str, Any]:
    """Delete a note by search."""
    matches = await search_notes(query, limit=3)
    
    if not matches:
        return {"status": "not_found", "message": f"No notes found matching: {query}"}
    
    if len(matches) > 1:
        return {
            "status": "multiple_matches",
            "message": "Multiple notes found. Please be more specific.",
            "matches": [{"id": m["id"], "title": m["title"]} for m in matches]
        }
    
    # Single match - delete it
    note = matches[0]
    db = _get_supabase()
    
    db.table("notes").update({
        "deleted": True
    }).eq("id", note["id"]).execute()
    
    return {
        "status": "deleted",
        "note_id": note["id"],
        "title": note["title"]
    }


# =============================================================================
# Reminders Tools
# =============================================================================

@tool(
    name="create_reminder",
    description="""Create a reminder or task. Parses natural language time:
    - "tomorrow 9am"
    - "in 2 hours"
    - "Friday at 3pm"
    - "next week"
    
    If no time specified, creates as a task without due date."""
)
async def create_reminder(
    task: str,
    due_time: Optional[str] = None
) -> Dict[str, Any]:
    """
    Create a reminder with natural language time parsing.
    """
    db = _get_supabase()
    
    reminder_id = str(uuid4())
    now = datetime.now(timezone.utc)
    
    # Parse due time
    due_at = None
    if due_time:
        due_at = _parse_natural_time(due_time)
    
    db.table("action_items").insert({
        "id": reminder_id,
        "user_id": "current_user",
        "title": task,
        "due_at": due_at.isoformat() if due_at else None,
        "status": "pending",
        "created_at": now.isoformat()
    }).execute()
    
    response = {
        "status": "created",
        "reminder_id": reminder_id,
        "task": task
    }
    
    if due_at:
        response["due_at"] = due_at.isoformat()
        response["message"] = f"Reminder set: '{task}' at {due_at.strftime('%B %d, %I:%M %p')}"
    else:
        response["message"] = f"Task created: '{task}'"
    
    return response


@tool(
    name="get_reminders",
    description="""Get user's tasks and reminders. Use when:
    - User asks "what are my tasks?"
    - User asks "show my reminders"
    - User asks "what's due today?"
    """
)
async def get_reminders(include_completed: bool = False) -> List[Dict[str, Any]]:
    """Get reminders/tasks."""
    db = _get_supabase()
    
    query = db.table("action_items")\
        .select("id, title, due_at, status, completed_at, created_at")\
        .order("due_at", desc=False)
    
    if not include_completed:
        query = query.eq("status", "pending")
    
    result = query.execute()
    
    return result.data


@tool(
    name="complete_reminder",
    description="""Mark a reminder as complete. Requires reminder_id.
    Use get_reminders first to find the ID."""
)
async def complete_reminder(reminder_id: str) -> Dict[str, Any]:
    """Mark reminder as complete."""
    db = _get_supabase()
    
    db.table("action_items").update({
        "status": "completed",
        "completed_at": datetime.now(timezone.utc).isoformat()
    }).eq("id", reminder_id).execute()
    
    return {"status": "completed", "reminder_id": reminder_id}


# =============================================================================
# Utility Tools
# =============================================================================

@tool(
    name="get_current_time",
    description="""Get current date and time. Use when:
    - User mentions time-related things
    - Need context for scheduling
    - Understanding "today", "tomorrow", etc."""
)
async def get_current_time() -> Dict[str, Any]:
    """Get current time with various formats in user's timezone (Pacific Time)."""
    # User's timezone: Pacific Time (PT)
    # UTC-8 in standard time, UTC-7 in daylight saving time
    import os

    # Get timezone offset from env or default to Pacific (-8 hours)
    tz_offset = int(os.environ.get("USER_TIMEZONE_OFFSET", "-8"))
    user_tz = timezone(timedelta(hours=tz_offset))

    now = datetime.now(user_tz)

    return {
        "iso": now.isoformat(),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "day": now.strftime("%A"),
        "formatted": now.strftime("%B %d, %Y at %I:%M %p"),
        "timestamp": int(now.timestamp()),
        "timezone": "PT" if tz_offset == -8 else f"UTC{tz_offset:+d}"
    }


# =============================================================================
# Helper Functions
# =============================================================================

async def _find_similar_note(topic: str) -> Optional[Dict[str, Any]]:
    """
    Find existing note with similar topic using hybrid search.

    Uses hybrid search (vector + keyword) with high threshold to avoid
    false positives. This prevents creating duplicate notes like
    "my coffee preferences" when one already exists.

    Based on Clawdbot's smart save pattern.
    """
    db = _get_supabase()

    # 1. Try hybrid search first (best approach - combines semantic + keyword)
    try:
        from database.vector_db import search_notes_hybrid

        # High threshold (0.6) to avoid matching unrelated notes
        # Hybrid search is better than pure vector because it also matches keywords
        results = search_notes_hybrid(
            query=topic,
            limit=3,
            min_score=0.6  # Require strong combined match
        )

        if results:
            # Return the best match (highest score)
            best = results[0]
            logger.info(f"Hybrid match found: '{best.get('title')}' (score: {best.get('score', 0):.2f})")
            return {
                "id": best.get("id"),
                "user_id": best.get("user_id"),
                "title": best.get("title"),
                "type": best.get("type", "collection")
            }

    except Exception as e:
        logger.warning(f"Hybrid search unavailable, using fallback: {e}")

    # 2. Fallback: Exact or near-exact title matching only
    # Much stricter than before - requires strong word overlap
    topic_lower = topic.lower().strip()
    topic_words = set(topic_lower.split())

    result = db.table("notes")\
        .select("id, user_id, title, type")\
        .eq("deleted", False)\
        .limit(50)\
        .execute()

    best_match = None
    best_score = 0

    for note in result.data or []:
        title_lower = note.get("title", "").lower().strip()
        title_words = set(title_lower.split())

        # Calculate Jaccard similarity (intersection / union)
        if topic_words and title_words:
            intersection = len(topic_words & title_words)
            union = len(topic_words | title_words)
            score = intersection / union if union > 0 else 0

            # Require at least 50% overlap (much stricter)
            if score > best_score and score >= 0.5:
                best_score = score
                best_match = note

    if best_match:
        logger.info(f"Fallback match found: '{best_match['title']}' (score: {best_score:.2f})")

    return best_match


def _get_user_timezone() -> timezone:
    """Get user's timezone (Pacific Time by default)."""
    import os
    tz_offset = int(os.environ.get("USER_TIMEZONE_OFFSET", "-8"))
    return timezone(timedelta(hours=tz_offset))


def _parse_natural_time(time_str: str) -> Optional[datetime]:
    """
    Parse natural language time expressions.

    Handles:
    - "tomorrow", "today"
    - "in X hours/minutes"
    - "next week/month"
    - "Monday", "Tuesday", etc.
    - "9am", "3:30pm"
    """
    user_tz = _get_user_timezone()
    now = datetime.now(user_tz)
    time_lower = time_str.lower().strip()
    
    # Relative time patterns
    if match := re.search(r"in (\d+) (hour|minute|day|week)s?", time_lower):
        amount = int(match.group(1))
        unit = match.group(2)
        
        if unit == "minute":
            return now + timedelta(minutes=amount)
        elif unit == "hour":
            return now + timedelta(hours=amount)
        elif unit == "day":
            return now + timedelta(days=amount)
        elif unit == "week":
            return now + timedelta(weeks=amount)
    
    # Relative days
    if "tomorrow" in time_lower:
        result = now + timedelta(days=1)
        return _apply_time_if_present(result, time_lower)
    
    if "today" in time_lower:
        return _apply_time_if_present(now, time_lower)
    
    if "next week" in time_lower:
        return now + timedelta(weeks=1)
    
    # Day names
    day_names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    for i, day in enumerate(day_names):
        if day in time_lower:
            current_day = now.weekday()
            days_ahead = (i - current_day) % 7
            if days_ahead == 0:
                days_ahead = 7  # Next week's same day
            result = now + timedelta(days=days_ahead)
            return _apply_time_if_present(result, time_lower)
    
    # Try to extract just a time
    if match := re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", time_lower):
        hour = int(match.group(1))
        minute = int(match.group(2)) if match.group(2) else 0
        period = match.group(3)
        
        if period == "pm" and hour < 12:
            hour += 12
        elif period == "am" and hour == 12:
            hour = 0
        
        return now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    
    return None


def _apply_time_if_present(dt: datetime, time_str: str) -> datetime:
    """Apply time from string to datetime if present."""
    if match := re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", time_str.lower()):
        hour = int(match.group(1))
        minute = int(match.group(2)) if match.group(2) else 0
        period = match.group(3)
        
        if period == "pm" and hour < 12:
            hour += 12
        elif period == "am" and hour == 12:
            hour = 0
        
        return dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
    
    # Default to 9am if no time specified
    return dt.replace(hour=9, minute=0, second=0, microsecond=0)
