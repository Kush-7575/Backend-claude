"""
Calendar Integration Tools

Tools for viewing and creating Google Calendar events.
Requires GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.
"""
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone, timedelta

from core.config import settings
from tools.registry import tool

logger = logging.getLogger("brainmap.tools.calendar")


def is_calendar_available() -> bool:
    """Check if Calendar integration is configured."""
    return bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET)


@tool(
    name="calendar_list_events",
    description="""List upcoming calendar events.
    Use when user asks about their schedule or what's coming up."""
)
async def calendar_list_events(
    days_ahead: int = 7,
    max_results: int = 10
) -> Dict[str, Any]:
    """
    List upcoming calendar events.
    
    Args:
        days_ahead: Number of days to look ahead
        max_results: Maximum events to return
    
    Returns:
        List of upcoming events
    """
    if not is_calendar_available():
        return {"error": "Calendar not configured", "events": []}
    
    # TODO: Implement Google Calendar API integration
    # For now, return placeholder
    return {
        "message": "Calendar integration requires user OAuth. Please connect Google Calendar in settings.",
        "events": []
    }


@tool(
    name="calendar_create_event",
    description="""Create a calendar event.
    Use when user wants to schedule something on their calendar."""
)
async def calendar_create_event(
    title: str,
    start_time: str,
    duration_minutes: int = 60,
    description: str = ""
) -> Dict[str, Any]:
    """
    Create a calendar event.
    
    Args:
        title: Event title
        start_time: ISO format datetime
        duration_minutes: Duration in minutes
        description: Optional description
    
    Returns:
        Created event info
    """
    if not is_calendar_available():
        return {"error": "Calendar not configured"}
    
    # TODO: Implement Google Calendar API integration
    return {
        "message": "Calendar integration requires user OAuth. Please connect Google Calendar in settings.",
        "status": "not_configured"
    }


@tool(
    name="calendar_check_availability",
    description="""Check if a time slot is available.
    Use when user asks if they're free at a certain time."""
)
async def calendar_check_availability(
    start_time: str,
    end_time: str
) -> Dict[str, Any]:
    """
    Check availability for a time slot.
    
    Args:
        start_time: ISO format start datetime
        end_time: ISO format end datetime
    
    Returns:
        Availability status and any conflicts
    """
    if not is_calendar_available():
        return {"error": "Calendar not configured", "available": None}
    
    # TODO: Implement Google Calendar API integration
    return {
        "message": "Calendar integration requires user OAuth.",
        "available": None
    }
