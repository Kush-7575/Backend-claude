"""
Lane Queueing System - Prevents Race Conditions

From Clawdbot's lanes.ts - ensures sequential processing of:
1. Per-session operations (prevents same-session conflicts)
2. Global operations (prevents cross-session conflicts during compaction)

This is CRITICAL for preventing:
- Session state corruption from concurrent requests
- Compaction conflicts
- Message ordering issues

Usage:
    async with session_lane(session_id):
        # Only one operation per session at a time
        async with global_lane():
            # Only one global operation at a time
            await do_work()
"""
import asyncio
import logging
from typing import Dict, Optional
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from collections import OrderedDict

logger = logging.getLogger("brainmap.lanes")

# Constants from Clawdbot
LANE_TIMEOUT_SECONDS = 300  # 5 minutes max wait
LANE_MAX_QUEUE_SIZE = 100  # Max pending operations per lane


@dataclass
class LaneStats:
    """Statistics for a lane."""
    total_operations: int = 0
    total_wait_time_ms: float = 0
    max_wait_time_ms: float = 0
    current_queue_size: int = 0
    last_operation_at: Optional[datetime] = None


class Lane:
    """
    A single processing lane with FIFO queue semantics.

    Only one operation can execute in a lane at a time.
    Other operations wait in queue.
    """

    def __init__(self, name: str, max_queue_size: int = LANE_MAX_QUEUE_SIZE):
        self.name = name
        self._lock = asyncio.Lock()
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue_size)
        self._stats = LaneStats()
        self._current_holder: Optional[str] = None

    @asynccontextmanager
    async def acquire(self, operation_id: str = "unknown", timeout: float = LANE_TIMEOUT_SECONDS):
        """
        Acquire the lane lock with timeout.

        Args:
            operation_id: Identifier for debugging
            timeout: Max seconds to wait for lock

        Raises:
            asyncio.TimeoutError: If lock not acquired within timeout
        """
        start_time = datetime.now(timezone.utc)
        self._stats.current_queue_size += 1

        try:
            # Try to acquire with timeout
            acquired = await asyncio.wait_for(
                self._lock.acquire(),
                timeout=timeout
            )

            if not acquired:
                raise asyncio.TimeoutError(f"Failed to acquire lane {self.name}")

            # Track stats
            wait_time = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            self._stats.total_wait_time_ms += wait_time
            self._stats.max_wait_time_ms = max(self._stats.max_wait_time_ms, wait_time)
            self._stats.total_operations += 1
            self._stats.last_operation_at = datetime.now(timezone.utc)
            self._current_holder = operation_id

            if wait_time > 1000:  # Log if waited more than 1 second
                logger.warning(
                    f"Lane {self.name} wait time: {wait_time:.0f}ms for {operation_id}"
                )

            logger.debug(f"Lane {self.name} acquired by {operation_id}")
            yield

        finally:
            self._stats.current_queue_size -= 1
            self._current_holder = None
            if self._lock.locked():
                self._lock.release()
                logger.debug(f"Lane {self.name} released by {operation_id}")

    @property
    def stats(self) -> LaneStats:
        return self._stats

    @property
    def is_busy(self) -> bool:
        return self._lock.locked()


class LaneManager:
    """
    Manages multiple lanes for different scopes.

    From Clawdbot patterns:
    - Session lanes: One per session, prevents concurrent ops on same session
    - Global lane: One global, used during compaction to prevent conflicts
    """

    def __init__(self):
        self._session_lanes: Dict[str, Lane] = {}
        self._global_lane = Lane("global")
        self._cleanup_lock = asyncio.Lock()
        self._max_session_lanes = 1000  # Limit memory usage

    def get_session_lane(self, session_id: str) -> Lane:
        """Get or create a lane for a session."""
        if session_id not in self._session_lanes:
            # Check if we need cleanup
            if len(self._session_lanes) >= self._max_session_lanes:
                asyncio.create_task(self._cleanup_old_lanes())

            self._session_lanes[session_id] = Lane(f"session:{session_id}")

        return self._session_lanes[session_id]

    @property
    def global_lane(self) -> Lane:
        """Get the global lane."""
        return self._global_lane

    async def _cleanup_old_lanes(self) -> None:
        """Remove lanes that haven't been used recently."""
        async with self._cleanup_lock:
            if len(self._session_lanes) < self._max_session_lanes:
                return  # Another task already cleaned up

            # Find lanes to remove (not busy and oldest)
            removable = [
                (sid, lane) for sid, lane in self._session_lanes.items()
                if not lane.is_busy
            ]

            # Sort by last operation time
            removable.sort(
                key=lambda x: x[1].stats.last_operation_at or datetime.min.replace(tzinfo=timezone.utc)
            )

            # Remove oldest half
            to_remove = removable[:len(removable) // 2]
            for sid, _ in to_remove:
                del self._session_lanes[sid]

            logger.info(f"Cleaned up {len(to_remove)} old session lanes")

    def get_stats(self) -> Dict[str, any]:
        """Get statistics for all lanes."""
        return {
            "global_lane": {
                "is_busy": self._global_lane.is_busy,
                "stats": self._global_lane.stats.__dict__
            },
            "session_lanes_count": len(self._session_lanes),
            "busy_session_lanes": sum(1 for l in self._session_lanes.values() if l.is_busy)
        }


# Singleton instance
_lane_manager: Optional[LaneManager] = None


def get_lane_manager() -> LaneManager:
    """Get the global lane manager instance."""
    global _lane_manager
    if _lane_manager is None:
        _lane_manager = LaneManager()
    return _lane_manager


@asynccontextmanager
async def session_lane(session_id: str, operation_id: str = "unknown"):
    """
    Context manager for session-scoped operations.

    Ensures only one operation runs per session at a time.

    Usage:
        async with session_lane(session.id, "chat_request"):
            await process_message(...)
    """
    manager = get_lane_manager()
    lane = manager.get_session_lane(session_id)

    async with lane.acquire(operation_id):
        yield


@asynccontextmanager
async def global_lane(operation_id: str = "unknown"):
    """
    Context manager for global operations.

    Used during compaction to prevent conflicts with other sessions.

    Usage:
        async with global_lane("compaction"):
            await compact_session(...)
    """
    manager = get_lane_manager()

    async with manager.global_lane.acquire(operation_id):
        yield


@asynccontextmanager
async def session_and_global_lane(session_id: str, operation_id: str = "unknown"):
    """
    Context manager for operations needing both session and global locks.

    Pattern from Clawdbot: enqueueCommandInLane(sessionLane, () => enqueueGlobal(...))

    Acquires session lane first, then global lane.
    This prevents deadlocks while ensuring both scopes are protected.

    Usage:
        async with session_and_global_lane(session.id, "compaction"):
            await compact_session(...)
    """
    async with session_lane(session_id, f"{operation_id}:session"):
        async with global_lane(f"{operation_id}:global"):
            yield


class SessionWriteLock:
    """
    Explicit write lock for session modifications.

    From Clawdbot's acquireSessionWriteLock pattern.
    Use this when modifying session state that must be atomic.
    """

    def __init__(self, session_id: str):
        self.session_id = session_id
        self._manager = get_lane_manager()

    async def __aenter__(self):
        self._lane = self._manager.get_session_lane(self.session_id)
        await self._lane._lock.acquire()
        logger.debug(f"Session write lock acquired: {self.session_id}")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._lane._lock.locked():
            self._lane._lock.release()
            logger.debug(f"Session write lock released: {self.session_id}")
        return False


def acquire_session_write_lock(session_id: str) -> SessionWriteLock:
    """
    Acquire a write lock for a session.

    From Clawdbot: acquireSessionWriteLock({ sessionFile })

    Usage:
        async with acquire_session_write_lock(session.id):
            session.messages.append(...)
            await save_session(session)
    """
    return SessionWriteLock(session_id)
