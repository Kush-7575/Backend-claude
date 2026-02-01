"""
EventStream - Async Event Stream with Completion Detection

Adapted from Clawdbot's pi-ai/src/utils/event-stream.ts

Provides a proper async iterable event stream with:
- Queue-based buffering for backpressure
- Completion detection via predicate
- Final result extraction
- Proper async iteration support

This replaces the simple StreamChunk dataclass with a proper
streaming abstraction that matches pi-agent's patterns.
"""
import asyncio
import logging
from typing import TypeVar, Generic, Callable, Optional, List, Any
from dataclasses import dataclass, field
from enum import Enum, auto

logger = logging.getLogger("brainmap.event_stream")

T = TypeVar('T')  # Event type
R = TypeVar('R')  # Result type


class EventType(Enum):
    """Standard agent event types (from pi-agent)."""
    # Agent lifecycle
    AGENT_START = auto()
    AGENT_END = auto()
    
    # Turn lifecycle - a turn is one assistant response + any tool calls/results
    TURN_START = auto()
    TURN_END = auto()
    
    # Message lifecycle
    MESSAGE_START = auto()
    MESSAGE_UPDATE = auto()
    MESSAGE_END = auto()
    
    # Tool execution lifecycle
    TOOL_EXECUTION_START = auto()
    TOOL_EXECUTION_UPDATE = auto()
    TOOL_EXECUTION_END = auto()
    
    # Stream content
    TEXT = auto()
    TEXT_DELTA = auto()
    THINKING_START = auto()
    THINKING_DELTA = auto()
    THINKING_END = auto()
    
    # Errors and completion
    ERROR = auto()
    DONE = auto()


@dataclass
class AgentEvent:
    """
    Standard agent event (from pi-agent).
    
    Matches the event types from pi-agent's types.ts:
    - agent_start / agent_end
    - turn_start / turn_end
    - message_start / message_update / message_end
    - tool_execution_start / tool_execution_update / tool_execution_end
    """
    type: str
    data: Optional[Any] = None
    message: Optional[Any] = None
    tool_call_id: Optional[str] = None
    tool_name: Optional[str] = None
    args: Optional[Any] = None
    result: Optional[Any] = None
    is_error: bool = False
    delta: Optional[str] = None
    
    def to_stream_chunk(self) -> dict:
        """Convert to legacy StreamChunk format for backwards compatibility."""
        return {
            "type": self.type,
            "content": self.delta or self.data or "",
            "metadata": {
                k: v for k, v in {
                    "message": self.message,
                    "tool_call_id": self.tool_call_id,
                    "tool_name": self.tool_name,
                    "args": self.args,
                    "result": self.result,
                    "is_error": self.is_error if self.is_error else None,
                }.items() if v is not None
            } or None
        }


class EventStream(Generic[T, R]):
    """
    Generic event stream for async iteration.
    
    From Clawdbot's pi-ai/src/utils/event-stream.ts
    
    Provides:
    - push(event): Add event to stream
    - end(result): Complete stream with final result
    - async iteration: for await event in stream
    - result(): Get final result when stream ends
    """
    
    def __init__(
        self,
        is_complete: Callable[[T], bool],
        extract_result: Callable[[T], R],
        max_queue_size: int = 1000
    ):
        """
        Initialize event stream.
        
        Args:
            is_complete: Predicate to detect completion event
            extract_result: Function to extract result from completion event
            max_queue_size: Max buffered events before blocking
        """
        self._is_complete = is_complete
        self._extract_result = extract_result
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue_size)
        self._done = False
        self._final_result: Optional[R] = None
        self._result_event = asyncio.Event()
        
    def push(self, event: T) -> None:
        """
        Push event to stream.
        
        If event is a completion event, marks stream as done.
        """
        if self._done:
            return
        
        if self._is_complete(event):
            self._done = True
            self._final_result = self._extract_result(event)
            self._result_event.set()
        
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("Event stream queue full, dropping event")
    
    def end(self, result: Optional[R] = None) -> None:
        """
        End the stream.
        
        Called after push() to signal iteration should stop.
        """
        self._done = True
        if result is not None:
            self._final_result = result
        self._result_event.set()
        
        # Push sentinel to unblock waiting consumers
        try:
            self._queue.put_nowait(None)
        except asyncio.QueueFull:
            pass
    
    def __aiter__(self):
        return self
    
    async def __anext__(self) -> T:
        """Get next event, raises StopAsyncIteration when done."""
        while True:
            try:
                # Try to get immediately
                event = self._queue.get_nowait()
                if event is None and self._done:
                    raise StopAsyncIteration
                if event is not None:
                    return event
            except asyncio.QueueEmpty:
                if self._done:
                    raise StopAsyncIteration
                
                # Wait for new event
                try:
                    event = await asyncio.wait_for(
                        self._queue.get(),
                        timeout=0.1
                    )
                    if event is None and self._done:
                        raise StopAsyncIteration
                    if event is not None:
                        return event
                except asyncio.TimeoutError:
                    if self._done:
                        raise StopAsyncIteration
                    continue
    
    async def result(self) -> R:
        """
        Get final result when stream ends.
        
        Blocks until stream is complete.
        """
        await self._result_event.wait()
        return self._final_result
    
    @property
    def is_done(self) -> bool:
        """Check if stream is complete."""
        return self._done


class AgentEventStream(EventStream[AgentEvent, List[Any]]):
    """
    Specialized event stream for agent events.
    
    Completes on agent_end event and extracts messages.
    """
    
    def __init__(self):
        super().__init__(
            is_complete=lambda e: e.type == "agent_end",
            extract_result=lambda e: e.data if e.data else []
        )


def create_agent_event_stream() -> AgentEventStream:
    """Factory function for agent event stream."""
    return AgentEventStream()


# Legacy compatibility - StreamChunk as before but enhanced
@dataclass
class StreamChunk:
    """
    A chunk of streaming response.
    
    Enhanced to support pi-agent event types while maintaining
    backwards compatibility.
    """
    type: str  # Event type (text, tool_start, tool_end, error, done, lifecycle, etc.)
    content: str
    metadata: Optional[dict] = None
    
    # Enhanced fields for pi-agent compatibility
    delta: Optional[str] = None
    message: Optional[Any] = None
    tool_call_id: Optional[str] = None
    tool_name: Optional[str] = None
    
    @classmethod
    def from_agent_event(cls, event: AgentEvent) -> 'StreamChunk':
        """Create StreamChunk from AgentEvent."""
        chunk_data = event.to_stream_chunk()
        return cls(
            type=chunk_data["type"],
            content=chunk_data["content"],
            metadata=chunk_data.get("metadata"),
            delta=event.delta,
            message=event.message,
            tool_call_id=event.tool_call_id,
            tool_name=event.tool_name
        )
    
    def to_agent_event(self) -> AgentEvent:
        """Convert to AgentEvent."""
        return AgentEvent(
            type=self.type,
            data=self.content,
            delta=self.delta,
            message=self.message,
            tool_call_id=self.tool_call_id,
            tool_name=self.tool_name,
        )
