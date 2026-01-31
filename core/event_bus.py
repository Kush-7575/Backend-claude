"""
Simple in-memory event bus for SSE background announcements.
"""
from __future__ import annotations

import asyncio
from typing import Dict, Set, Any


class SessionEventBus:
    def __init__(self) -> None:
        self._subscribers: Dict[str, Set[asyncio.Queue]] = {}

    def subscribe(self, session_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(session_id, set()).add(queue)
        return queue

    def unsubscribe(self, session_id: str, queue: asyncio.Queue) -> None:
        subs = self._subscribers.get(session_id)
        if not subs:
            return
        subs.discard(queue)
        if not subs:
            self._subscribers.pop(session_id, None)

    async def publish(self, session_id: str, payload: Any) -> None:
        subs = self._subscribers.get(session_id)
        if not subs:
            return
        for q in list(subs):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                continue


_bus = SessionEventBus()


def get_event_bus() -> SessionEventBus:
    return _bus
