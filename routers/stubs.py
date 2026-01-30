"""
Stub Routers - Compatibility endpoints for Fable app

These endpoints return empty/default responses to prevent app crashes
while full implementation is pending.
"""
from fastapi import APIRouter
from typing import List, Dict, Any

# Today/Action Items Router
today_router = APIRouter(prefix="/v1/today", tags=["Today"])


@today_router.get("/all")
async def get_all_action_items() -> Dict[str, Any]:
    """Get all action items - stub returns empty list."""
    return {"items": []}


@today_router.get("/{item_id}")
async def get_action_item(item_id: str) -> Dict[str, Any]:
    """Get action item by ID - stub."""
    return {"id": item_id, "title": "", "completed": False}


@today_router.post("/{item_id}/complete")
async def complete_action_item(item_id: str) -> Dict[str, Any]:
    """Complete action item - stub."""
    return {"status": "ok"}


@today_router.post("/{item_id}/uncomplete")
async def uncomplete_action_item(item_id: str) -> Dict[str, Any]:
    """Uncomplete action item - stub."""
    return {"status": "ok"}


# Search Router
search_router = APIRouter(prefix="/v1/search", tags=["Search"])


@search_router.get("/quick")
async def quick_search(q: str = "") -> Dict[str, Any]:
    """Quick search - stub returns empty results."""
    return {"results": []}


@search_router.get("/notes")
async def search_notes(q: str = "") -> Dict[str, Any]:
    """Search notes - stub."""
    return {"notes": []}


@search_router.get("/memories")
async def search_memories(q: str = "") -> Dict[str, Any]:
    """Search memories - stub."""
    return {"memories": []}


# Memories Router (stub)
memories_router = APIRouter(prefix="/v1/memories", tags=["Memories"])


@memories_router.get("")
async def get_memories() -> Dict[str, Any]:
    """Get memories - stub."""
    return {"memories": []}


@memories_router.get("/recent")
async def get_recent_memories() -> Dict[str, Any]:
    """Get recent memories - stub."""
    return {"memories": []}


@memories_router.get("/important")
async def get_important_memories() -> Dict[str, Any]:
    """Get important memories - stub."""
    return {"memories": []}


@memories_router.get("/stats")
async def get_memory_stats() -> Dict[str, Any]:
    """Get memory stats - stub."""
    return {"total": 0, "reviewed": 0}


@memories_router.get("/{memory_id}")
async def get_memory(memory_id: str) -> Dict[str, Any]:
    """Get memory by ID - stub."""
    return {"id": memory_id, "content": "", "importance": 0}


@memories_router.post("/{memory_id}/review")
async def review_memory(memory_id: str) -> Dict[str, Any]:
    """Review memory - stub."""
    return {"status": "ok"}


@memories_router.delete("/{memory_id}")
async def delete_memory(memory_id: str) -> Dict[str, Any]:
    """Delete memory - stub."""
    return {"status": "ok"}


# Notifications Router (stub)
notifications_router = APIRouter(prefix="/v1/notifications", tags=["Notifications"])


@notifications_router.get("")
async def get_notifications() -> Dict[str, Any]:
    """Get notifications - stub."""
    return {"notifications": []}


@notifications_router.get("/unread-count")
async def get_unread_count() -> Dict[str, Any]:
    """Get unread count - stub."""
    return {"count": 0}


@notifications_router.post("/{notification_id}/read")
async def mark_notification_read(notification_id: str) -> Dict[str, Any]:
    """Mark notification read - stub."""
    return {"status": "ok"}


@notifications_router.post("/read-all")
async def mark_all_read() -> Dict[str, Any]:
    """Mark all notifications read - stub."""
    return {"status": "ok"}


@notifications_router.post("/register-device")
async def register_device() -> Dict[str, Any]:
    """Register device for push - stub."""
    return {"status": "ok"}


# Apps/Plugins Router (stub)
apps_router = APIRouter(prefix="/v1/apps", tags=["Apps"])


@apps_router.get("/marketplace")
async def get_marketplace_apps() -> Dict[str, Any]:
    """Get marketplace apps - stub."""
    return {"apps": []}


@apps_router.get("/installed")
async def get_installed_apps() -> Dict[str, Any]:
    """Get installed apps - stub."""
    return {"apps": []}


@apps_router.get("/my-apps")
async def get_my_apps() -> Dict[str, Any]:
    """Get my apps - stub."""
    return {"apps": []}


@apps_router.post("/{app_id}/install")
async def install_app(app_id: str) -> Dict[str, Any]:
    """Install app - stub."""
    return {"status": "ok"}


@apps_router.post("/{app_id}/uninstall")
async def uninstall_app(app_id: str) -> Dict[str, Any]:
    """Uninstall app - stub."""
    return {"status": "ok"}


@apps_router.post("/{app_id}/enable")
async def enable_app(app_id: str) -> Dict[str, Any]:
    """Enable app - stub."""
    return {"status": "ok"}


@apps_router.post("/{app_id}/disable")
async def disable_app(app_id: str) -> Dict[str, Any]:
    """Disable app - stub."""
    return {"status": "ok"}


# Integrations Router (stub)
integrations_router = APIRouter(prefix="/v1/integrations", tags=["Integrations"])


@integrations_router.get("/status")
async def get_integrations_status() -> Dict[str, Any]:
    """Get integrations status - stub."""
    return {
        "notion": {"connected": False},
        "google_calendar": {"connected": False},
        "gmail": {"connected": False}
    }
