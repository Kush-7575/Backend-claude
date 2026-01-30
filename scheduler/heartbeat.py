"""
Heartbeat Runner - Proactive Assistant Checks

Implements Clawdbot's heartbeat pattern:
1. Periodic checks (every N minutes)
2. Calendar awareness (upcoming events)
3. Reminder checks (due soon)
4. Inbox checks (unread items)
5. Proactive notifications
6. HEARTBEAT_OK token handling (Clawdbot pattern)

This is what makes BrainMap a "proactive" assistant,
not just a reactive chatbot.
"""
import logging
from typing import Optional, List, Dict, Any, Callable
from datetime import datetime, timezone, timedelta
import asyncio

from core.config import settings

logger = logging.getLogger("brainmap.scheduler.heartbeat")

# Heartbeat prompt constants from Clawdbot (heartbeat.ts)
HEARTBEAT_OK = "[HEARTBEAT_OK]"

HEARTBEAT_PROMPT = """## Heartbeat Check

This is an automated heartbeat check. Review the following and determine if any proactive action is needed.

### Current Time
{current_time}

### Upcoming Reminders (next 30 min)
{reminders}

### Upcoming Calendar Events (next hour)
{calendar}

### Instructions
1. If there are reminders due very soon (< 5 min), notify the user proactively
2. If there are calendar events about to start, give a heads-up
3. If nothing needs attention, respond with exactly: [HEARTBEAT_OK]

The [HEARTBEAT_OK] response will be filtered and the user won't see anything.
Only generate visible output if you need to proactively notify the user about something important."""


def is_heartbeat_ok(response: str) -> bool:
    """
    Check if response is the heartbeat OK token.

    From Clawdbot: isHeartbeatContentEffectivelyEmpty()
    Returns True if we should suppress this response (no action needed).
    """
    if not response:
        return True
    cleaned = response.strip()
    return cleaned == HEARTBEAT_OK or cleaned == ""


def build_heartbeat_prompt(
    reminders: List[Dict[str, Any]],
    calendar: List[Dict[str, Any]]
) -> str:
    """
    Build the heartbeat check prompt.

    Args:
        reminders: List of upcoming reminders
        calendar: List of upcoming calendar events

    Returns:
        Formatted heartbeat prompt
    """
    current_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if reminders:
        reminders_text = "\n".join([
            f"- {r.get('title', 'Untitled')} (due: {r.get('due_at', 'unknown')})"
            for r in reminders
        ])
    else:
        reminders_text = "(none)"

    if calendar:
        calendar_text = "\n".join([
            f"- {e.get('title', 'Untitled')} (at: {e.get('start_time', 'unknown')})"
            for e in calendar
        ])
    else:
        calendar_text = "(none)"

    return HEARTBEAT_PROMPT.format(
        current_time=current_time,
        reminders=reminders_text,
        calendar=calendar_text
    )


class HeartbeatRunner:
    """
    Runs periodic checks and triggers proactive actions.
    
    Checks:
    - Reminders due soon (next 30 min)
    - Calendar events upcoming
    - Memory items to review
    """
    
    def __init__(
        self,
        supabase_client=None,
        notification_handler: Optional[Callable] = None
    ):
        """
        Initialize heartbeat runner.
        
        Args:
            supabase_client: Supabase client for data access
            notification_handler: Callback for sending notifications
        """
        self._supabase = supabase_client
        self._notify = notification_handler
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._interval = settings.HEARTBEAT_INTERVAL_MINUTES * 60
    
    async def start(self):
        """Start the heartbeat loop."""
        if self._running:
            logger.warning("Heartbeat already running")
            return
        
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(f"Heartbeat started (interval: {settings.HEARTBEAT_INTERVAL_MINUTES} min)")
    
    async def stop(self):
        """Stop the heartbeat loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Heartbeat stopped")
    
    async def _run_loop(self):
        """Main heartbeat loop."""
        while self._running:
            try:
                await self._run_checks()
            except Exception as e:
                logger.error(f"Heartbeat check error: {e}")
            
            await asyncio.sleep(self._interval)
    
    async def _run_checks(self):
        """Run all heartbeat checks."""
        logger.debug("Running heartbeat checks...")
        
        results = {
            "reminders": await self._check_reminders(),
            "calendar": await self._check_calendar(),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        # Aggregate important items
        important = []
        
        # Reminders due soon
        if results["reminders"]:
            for r in results["reminders"]:
                important.append({
                    "type": "reminder",
                    "message": f"Reminder due soon: {r['title']}",
                    "data": r
                })
        
        # Calendar events upcoming
        if results["calendar"]:
            for e in results["calendar"]:
                important.append({
                    "type": "calendar",
                    "message": f"Upcoming event: {e['title']}",
                    "data": e
                })
        
        # Send notifications if we have a handler
        if important and self._notify:
            await self._notify(important)
        
        return results
    
    async def _check_reminders(self) -> List[Dict[str, Any]]:
        """Check for reminders due in the next 30 minutes."""
        if not self._supabase:
            return []
        
        try:
            now = datetime.now(timezone.utc)
            soon = now + timedelta(minutes=30)
            
            result = self._supabase.table("action_items")\
                .select("id, title, due_at")\
                .eq("completed", False)\
                .gte("due_at", now.isoformat())\
                .lte("due_at", soon.isoformat())\
                .execute()
            
            return result.data
            
        except Exception as e:
            logger.error(f"Reminder check failed: {e}")
            return []
    
    async def _check_calendar(self) -> List[Dict[str, Any]]:
        """Check for calendar events in the next hour."""
        # TODO: Integrate with Google Calendar API
        return []
    
    async def force_check(self) -> Dict[str, Any]:
        """Force an immediate heartbeat check."""
        return await self._run_checks()


# Module-level instance
_heartbeat: Optional[HeartbeatRunner] = None


async def start_heartbeat(supabase_client=None, notification_handler=None):
    """Start the heartbeat runner."""
    global _heartbeat
    if _heartbeat is None:
        _heartbeat = HeartbeatRunner(supabase_client, notification_handler)
    await _heartbeat.start()


async def stop_heartbeat():
    """Stop the heartbeat runner."""
    global _heartbeat
    if _heartbeat:
        await _heartbeat.stop()
