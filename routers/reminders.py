"""
Reminders Router - Tasks and Reminders CRUD

REST endpoints for reminders/tasks management.
"""
import logging
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from database.client import table

logger = logging.getLogger("brainmap.routers.reminders")

router = APIRouter()


# =============================================================================
# Models
# =============================================================================

class CreateReminderRequest(BaseModel):
    """Request to create a reminder."""
    title: str = Field(..., min_length=1)
    due_at: Optional[str] = None
    description: Optional[str] = None


class UpdateReminderRequest(BaseModel):
    """Request to update a reminder."""
    title: Optional[str] = None
    due_at: Optional[str] = None
    completed: Optional[bool] = None


class ReminderResponse(BaseModel):
    """Reminder response."""
    id: str
    title: str
    due_at: Optional[str] = None
    completed: bool = False
    created_at: Optional[str] = None


# =============================================================================
# Endpoints
# =============================================================================

@router.get("", response_model=List[ReminderResponse])
async def list_reminders(
    limit: int = Query(50, ge=1, le=100),
    include_completed: bool = False
):
    """Get reminders/tasks."""
    query = table("action_items")\
        .select("*")\
        .order("due_at", desc=False)
    
    if not include_completed:
        query = query.eq("completed", False)
    
    result = query.limit(limit).execute()
    
    return [ReminderResponse(
        id=row["id"],
        title=row["title"],
        due_at=row.get("due_at"),
        completed=row.get("completed", False),
        created_at=row.get("created_at")
    ) for row in result.data]


@router.get("/{reminder_id}", response_model=ReminderResponse)
async def get_reminder(reminder_id: str):
    """Get a specific reminder."""
    result = table("action_items")\
        .select("*")\
        .eq("id", reminder_id)\
        .execute()
    
    if not result.data:
        raise HTTPException(status_code=404, detail="Reminder not found")
    
    row = result.data[0]
    return ReminderResponse(
        id=row["id"],
        title=row["title"],
        due_at=row.get("due_at"),
        completed=row.get("completed", False),
        created_at=row.get("created_at")
    )


@router.post("", response_model=ReminderResponse, status_code=201)
async def create_reminder(request: CreateReminderRequest):
    """Create a new reminder/task."""
    from uuid import uuid4
    from datetime import datetime, timezone
    
    reminder_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()
    
    table("action_items").insert({
        "id": reminder_id,
        "user_id": "current_user",
        "title": request.title,
        "due_at": request.due_at,
        "completed": False,
        "created_at": now
    }).execute()
    
    return ReminderResponse(
        id=reminder_id,
        title=request.title,
        due_at=request.due_at,
        completed=False,
        created_at=now
    )


@router.patch("/{reminder_id}", response_model=ReminderResponse)
async def update_reminder(reminder_id: str, request: UpdateReminderRequest):
    """Update a reminder."""
    update_data = request.model_dump(exclude_none=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")
    
    result = table("action_items")\
        .update(update_data)\
        .eq("id", reminder_id)\
        .execute()
    
    if not result.data:
        raise HTTPException(status_code=404, detail="Reminder not found")
    
    return await get_reminder(reminder_id)


@router.post("/{reminder_id}/complete", response_model=ReminderResponse)
async def complete_reminder(reminder_id: str):
    """Mark a reminder as complete."""
    from datetime import datetime, timezone
    
    result = table("action_items")\
        .update({
            "completed": True,
            "completed_at": datetime.now(timezone.utc).isoformat()
        })\
        .eq("id", reminder_id)\
        .execute()
    
    if not result.data:
        raise HTTPException(status_code=404, detail="Reminder not found")
    
    return await get_reminder(reminder_id)


@router.delete("/{reminder_id}", status_code=204)
async def delete_reminder(reminder_id: str):
    """Delete a reminder."""
    result = table("action_items")\
        .delete()\
        .eq("id", reminder_id)\
        .execute()
    
    if not result.data:
        raise HTTPException(status_code=404, detail="Reminder not found")
