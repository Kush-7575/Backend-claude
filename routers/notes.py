"""
Notes Router - Notes CRUD with Search

REST endpoints for notes management.
"""
import logging
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from database.client import table

logger = logging.getLogger("brainmap.routers.notes")

router = APIRouter()


# =============================================================================
# Models
# =============================================================================

class CreateNoteRequest(BaseModel):
    """Request to create a note."""
    title: str = Field(..., min_length=1)
    content: str = Field("")
    type: str = Field("collection")


class UpdateNoteRequest(BaseModel):
    """Request to update a note."""
    title: Optional[str] = None
    pinned: Optional[bool] = None


class AddItemRequest(BaseModel):
    """Request to add item to note."""
    content: str = Field(..., min_length=1)


class NoteResponse(BaseModel):
    """Note response."""
    id: str
    title: str
    type: str
    items: List[dict] = []
    pinned: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


# =============================================================================
# Endpoints
# =============================================================================

@router.get("", response_model=List[NoteResponse])
async def list_notes(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    include_archived: bool = False
):
    """Get all notes with pagination."""
    query = table("notes")\
        .select("*, note_items(*)")\
        .eq("deleted", False)\
        .order("pinned", desc=True)\
        .order("updated_at", desc=True)\
        .range(offset, offset + limit - 1)
    
    if not include_archived:
        query = query.eq("archived", False)
    
    result = query.execute()
    
    notes = []
    for row in result.data:
        notes.append(NoteResponse(
            id=row["id"],
            title=row["title"],
            type=row.get("type", "collection"),
            items=row.get("note_items", []),
            pinned=row.get("pinned", False),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at")
        ))
    
    return notes


@router.get("/{note_id}", response_model=NoteResponse)
async def get_note(note_id: str):
    """Get a specific note with items."""
    result = table("notes")\
        .select("*, note_items(*)")\
        .eq("id", note_id)\
        .eq("deleted", False)\
        .execute()
    
    if not result.data:
        raise HTTPException(status_code=404, detail="Note not found")
    
    row = result.data[0]
    return NoteResponse(
        id=row["id"],
        title=row["title"],
        type=row.get("type", "collection"),
        items=row.get("note_items", []),
        pinned=row.get("pinned", False),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at")
    )


@router.post("", response_model=NoteResponse, status_code=201)
async def create_note(request: CreateNoteRequest):
    """Create a new note."""
    from uuid import uuid4
    from datetime import datetime, timezone
    
    note_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()
    user_id = "current_user"  # TODO: Get from auth
    
    # Create note
    table("notes").insert({
        "id": note_id,
        "user_id": user_id,
        "title": request.title,
        "type": request.type,
        "created_at": now,
        "updated_at": now
    }).execute()
    
    # Add initial content if provided
    items = []
    if request.content:
        item_id = str(uuid4())
        table("note_items").insert({
            "id": item_id,
            "note_id": note_id,
            "user_id": user_id,
            "content": request.content,
            "checked": False,
            "added_at": now
        }).execute()
        items = [{"id": item_id, "content": request.content}]
    
    return NoteResponse(
        id=note_id,
        title=request.title,
        type=request.type,
        items=items,
        created_at=now,
        updated_at=now
    )


@router.patch("/{note_id}", response_model=NoteResponse)
async def update_note(note_id: str, request: UpdateNoteRequest):
    """Update a note."""
    from datetime import datetime, timezone
    
    update_data = request.model_dump(exclude_none=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")
    
    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    
    result = table("notes")\
        .update(update_data)\
        .eq("id", note_id)\
        .execute()
    
    if not result.data:
        raise HTTPException(status_code=404, detail="Note not found")
    
    # Fetch updated note with items
    return await get_note(note_id)


@router.delete("/{note_id}", status_code=204)
async def delete_note(note_id: str):
    """Soft delete a note."""
    result = table("notes")\
        .update({"deleted": True})\
        .eq("id", note_id)\
        .execute()
    
    if not result.data:
        raise HTTPException(status_code=404, detail="Note not found")


@router.post("/{note_id}/items", status_code=201)
async def add_item_to_note(note_id: str, request: AddItemRequest):
    """Add an item to a note."""
    from uuid import uuid4
    from datetime import datetime, timezone
    
    # Verify note exists
    note_result = table("notes")\
        .select("id")\
        .eq("id", note_id)\
        .eq("deleted", False)\
        .execute()
    
    if not note_result.data:
        raise HTTPException(status_code=404, detail="Note not found")
    
    item_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()
    
    table("note_items").insert({
        "id": item_id,
        "note_id": note_id,
        "user_id": "current_user",
        "content": request.content,
        "checked": False,
        "added_at": now
    }).execute()
    
    # Update note timestamp
    table("notes")\
        .update({"updated_at": now})\
        .eq("id", note_id)\
        .execute()
    
    return {"id": item_id, "content": request.content, "added_at": now}


@router.delete("/{note_id}/items/{item_id}", status_code=204)
async def delete_item_from_note(note_id: str, item_id: str):
    """Delete an item from a note."""
    result = table("note_items")\
        .delete()\
        .eq("id", item_id)\
        .eq("note_id", note_id)\
        .execute()
    
    if not result.data:
        raise HTTPException(status_code=404, detail="Item not found")
