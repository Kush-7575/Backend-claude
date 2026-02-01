"""
Active Run Registry - Tracks Running Agent Sessions

From Clawdbot's runs.ts pattern. Enables:
1. Checking if a session is currently running
2. Queueing messages mid-run (user types while AI is responding)
3. Aborting running sessions
4. Waiting for run completion with timeout
5. Checking streaming/compaction state

This is critical for:
- Preventing duplicate requests
- Supporting multi-message conversations
- Graceful abort handling
- Run status queries
"""
import asyncio
import logging
from typing import Dict, Optional, Callable, Awaitable, Set
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger("brainmap.runs")


@dataclass
class RunHandle:
    """
    Handle to an active run, allowing control and status queries.
    
    From Clawdbot's EmbeddedPiQueueHandle type.
    """
    session_id: str
    run_id: str
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Callbacks for run control
    _queue_message: Optional[Callable[[str], Awaitable[None]]] = None
    _is_streaming: Optional[Callable[[], bool]] = None
    _is_compacting: Optional[Callable[[], bool]] = None
    _abort: Optional[Callable[[], None]] = None
    
    # Internal state
    _aborted: bool = False
    _pending_messages: list = field(default_factory=list)
    
    def is_streaming(self) -> bool:
        """Check if the run is currently streaming."""
        if self._is_streaming:
            return self._is_streaming()
        return True  # Assume streaming if no callback
    
    def is_compacting(self) -> bool:
        """Check if the run is currently compacting."""
        if self._is_compacting:
            return self._is_compacting()
        return False
    
    async def queue_message(self, text: str) -> bool:
        """
        Queue a message to be processed mid-run.
        
        Returns True if queued successfully, False if run not accepting messages.
        """
        if self._aborted:
            logger.debug(f"Queue message failed: session={self.session_id} reason=aborted")
            return False
        
        if not self.is_streaming():
            logger.debug(f"Queue message failed: session={self.session_id} reason=not_streaming")
            return False
        
        if self.is_compacting():
            logger.debug(f"Queue message failed: session={self.session_id} reason=compacting")
            return False
        
        if self._queue_message:
            await self._queue_message(text)
            logger.debug(f"Message queued: session={self.session_id} len={len(text)}")
            return True
        
        # Fallback: store in pending list
        self._pending_messages.append(text)
        logger.debug(f"Message stored in pending: session={self.session_id}")
        return True
    
    def abort(self) -> bool:
        """
        Abort the run.
        
        Returns True if abort was initiated, False if already aborted.
        """
        if self._aborted:
            return False
        
        self._aborted = True
        
        if self._abort:
            self._abort()
        
        logger.debug(f"Run aborted: session={self.session_id} run={self.run_id}")
        return True
    
    def is_aborted(self) -> bool:
        """Check if the run has been aborted."""
        return self._aborted
    
    def get_pending_messages(self) -> list:
        """Get and clear pending messages."""
        messages = self._pending_messages.copy()
        self._pending_messages.clear()
        return messages


@dataclass
class RunWaiter:
    """A waiter for run completion."""
    resolve: Callable[[bool], None]
    timer: asyncio.TimerHandle


class RunRegistry:
    """
    Registry of active agent runs.
    
    From Clawdbot's ACTIVE_EMBEDDED_RUNS pattern.
    """
    
    def __init__(self):
        self._active_runs: Dict[str, RunHandle] = {}
        self._waiters: Dict[str, Set[asyncio.Future]] = {}
        self._lock = asyncio.Lock()
    
    async def set_active_run(self, session_id: str, handle: RunHandle) -> None:
        """
        Register an active run for a session.
        
        Replaces any existing run for the session.
        """
        async with self._lock:
            was_active = session_id in self._active_runs
            self._active_runs[session_id] = handle
            
            if was_active:
                logger.debug(
                    f"Run replaced: session={session_id} run={handle.run_id} "
                    f"total_active={len(self._active_runs)}"
                )
            else:
                logger.debug(
                    f"Run registered: session={session_id} run={handle.run_id} "
                    f"total_active={len(self._active_runs)}"
                )
    
    async def clear_active_run(self, session_id: str, handle: RunHandle) -> None:
        """
        Clear an active run (only if it matches the provided handle).
        
        This prevents race conditions where a new run has already started.
        """
        async with self._lock:
            current = self._active_runs.get(session_id)
            
            if current is not handle:
                logger.debug(
                    f"Run clear skipped: session={session_id} reason=handle_mismatch"
                )
                return
            
            del self._active_runs[session_id]
            logger.debug(
                f"Run cleared: session={session_id} run={handle.run_id} "
                f"total_active={len(self._active_runs)}"
            )
            
            # Notify waiters
            await self._notify_waiters(session_id)
    
    async def _notify_waiters(self, session_id: str) -> None:
        """Notify all waiters that a run has ended."""
        waiters = self._waiters.pop(session_id, set())
        for future in waiters:
            if not future.done():
                future.set_result(True)
        
        if waiters:
            logger.debug(f"Notified {len(waiters)} waiters: session={session_id}")
    
    def is_run_active(self, session_id: str) -> bool:
        """Check if a session has an active run."""
        return session_id in self._active_runs
    
    def is_run_streaming(self, session_id: str) -> bool:
        """Check if a session's active run is streaming."""
        handle = self._active_runs.get(session_id)
        return handle.is_streaming() if handle else False
    
    def is_run_compacting(self, session_id: str) -> bool:
        """Check if a session's active run is compacting."""
        handle = self._active_runs.get(session_id)
        return handle.is_compacting() if handle else False
    
    def get_active_run(self, session_id: str) -> Optional[RunHandle]:
        """Get the active run handle for a session."""
        return self._active_runs.get(session_id)
    
    async def queue_message(self, session_id: str, text: str) -> bool:
        """
        Queue a message to an active run.
        
        From Clawdbot's queueEmbeddedPiMessage.
        """
        handle = self._active_runs.get(session_id)
        if not handle:
            logger.debug(f"Queue message failed: session={session_id} reason=no_active_run")
            return False
        
        return await handle.queue_message(text)
    
    def abort_run(self, session_id: str) -> bool:
        """
        Abort an active run.
        
        From Clawdbot's abortEmbeddedPiRun.
        """
        handle = self._active_runs.get(session_id)
        if not handle:
            logger.debug(f"Abort failed: session={session_id} reason=no_active_run")
            return False
        
        return handle.abort()
    
    async def wait_for_run_end(
        self,
        session_id: str,
        timeout_ms: int = 15000
    ) -> bool:
        """
        Wait for a run to complete.
        
        From Clawdbot's waitForEmbeddedPiRunEnd.
        
        Returns:
            True if run ended, False if timeout
        """
        if not session_id or session_id not in self._active_runs:
            return True
        
        logger.debug(f"Waiting for run end: session={session_id} timeout_ms={timeout_ms}")
        
        # Create a future for this waiter
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        
        # Register waiter
        if session_id not in self._waiters:
            self._waiters[session_id] = set()
        self._waiters[session_id].add(future)
        
        try:
            # Wait with timeout
            await asyncio.wait_for(future, timeout=timeout_ms / 1000)
            return True
        except asyncio.TimeoutError:
            logger.warning(f"Wait timeout: session={session_id} timeout_ms={timeout_ms}")
            return False
        finally:
            # Cleanup
            if session_id in self._waiters:
                self._waiters[session_id].discard(future)
                if not self._waiters[session_id]:
                    del self._waiters[session_id]
    
    def get_stats(self) -> Dict:
        """Get registry statistics."""
        return {
            "active_runs": len(self._active_runs),
            "waiting_sessions": len(self._waiters),
            "runs": [
                {
                    "session_id": sid,
                    "run_id": h.run_id,
                    "started_at": h.started_at.isoformat(),
                    "is_streaming": h.is_streaming(),
                    "is_compacting": h.is_compacting(),
                    "is_aborted": h.is_aborted(),
                    "pending_messages": len(h._pending_messages),
                }
                for sid, h in self._active_runs.items()
            ]
        }


# Singleton instance
_run_registry: Optional[RunRegistry] = None


def get_run_registry() -> RunRegistry:
    """Get the global run registry instance."""
    global _run_registry
    if _run_registry is None:
        _run_registry = RunRegistry()
    return _run_registry


# Convenience functions matching Clawdbot's API
async def set_active_run(session_id: str, handle: RunHandle) -> None:
    """Register an active run."""
    await get_run_registry().set_active_run(session_id, handle)


async def clear_active_run(session_id: str, handle: RunHandle) -> None:
    """Clear an active run."""
    await get_run_registry().clear_active_run(session_id, handle)


def is_run_active(session_id: str) -> bool:
    """Check if a session has an active run."""
    return get_run_registry().is_run_active(session_id)


def is_run_streaming(session_id: str) -> bool:
    """Check if a session's active run is streaming."""
    return get_run_registry().is_run_streaming(session_id)


async def queue_run_message(session_id: str, text: str) -> bool:
    """Queue a message to an active run."""
    return await get_run_registry().queue_message(session_id, text)


def abort_run(session_id: str) -> bool:
    """Abort an active run."""
    return get_run_registry().abort_run(session_id)


async def wait_for_run_end(session_id: str, timeout_ms: int = 15000) -> bool:
    """Wait for a run to complete."""
    return await get_run_registry().wait_for_run_end(session_id, timeout_ms)
