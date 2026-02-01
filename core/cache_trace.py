"""
Cache Trace - Diagnostic logging for prompt caching.

Adapted from Clawdbot's cache-trace.ts

Provides detailed logging of cache behavior across the session lifecycle:
- session:loaded - After loading session from DB
- session:sanitized - After sanitizing messages
- session:limited - After limiting history
- prompt:before - Before LLM call
- stream:context - During streaming
- session:after - After LLM call

This helps debug cache miss issues and optimize prompt caching.
"""
import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional
from pathlib import Path

logger = logging.getLogger("brainmap.cache_trace")


@dataclass
class CacheTraceEvent:
    """A single cache trace event."""
    ts: str
    seq: int
    stage: str
    run_id: Optional[str] = None
    session_id: Optional[str] = None
    provider: Optional[str] = None
    model_id: Optional[str] = None
    message_count: Optional[int] = None
    message_roles: Optional[List[str]] = None
    message_fingerprints: Optional[List[str]] = None
    messages_digest: Optional[str] = None
    system_digest: Optional[str] = None
    note: Optional[str] = None
    error: Optional[str] = None


class CacheTrace:
    """
    Cache trace logger for debugging prompt caching.
    
    From Clawdbot's cache-trace.ts.
    
    Enable via environment:
        BRAINMAP_CACHE_TRACE=1
        BRAINMAP_CACHE_TRACE_FILE=/path/to/trace.jsonl
    """
    
    def __init__(
        self,
        enabled: bool = False,
        file_path: Optional[str] = None,
        include_messages: bool = False,
        run_id: Optional[str] = None,
        session_id: Optional[str] = None,
        provider: Optional[str] = None,
        model_id: Optional[str] = None
    ):
        self._enabled = enabled
        self._file_path = file_path
        self._include_messages = include_messages
        self._run_id = run_id
        self._session_id = session_id
        self._provider = provider
        self._model_id = model_id
        self._seq = 0
    
    @classmethod
    def from_env(
        cls,
        run_id: Optional[str] = None,
        session_id: Optional[str] = None,
        provider: Optional[str] = None,
        model_id: Optional[str] = None
    ) -> 'CacheTrace':
        """Create cache trace from environment variables."""
        enabled = os.environ.get("BRAINMAP_CACHE_TRACE", "").lower() in ("1", "true", "yes")
        file_path = os.environ.get("BRAINMAP_CACHE_TRACE_FILE", "./logs/cache-trace.jsonl")
        include_messages = os.environ.get("BRAINMAP_CACHE_TRACE_MESSAGES", "").lower() in ("1", "true")
        
        return cls(
            enabled=enabled,
            file_path=file_path,
            include_messages=include_messages,
            run_id=run_id,
            session_id=session_id,
            provider=provider,
            model_id=model_id
        )
    
    def record_stage(
        self,
        stage: str,
        messages: Optional[List[Dict[str, Any]]] = None,
        system: Optional[str] = None,
        note: Optional[str] = None,
        error: Optional[str] = None
    ) -> None:
        """
        Record a cache trace event.
        
        Args:
            stage: One of session:loaded, session:sanitized, session:limited,
                   prompt:before, stream:context, session:after
            messages: Current message list (for fingerprinting)
            system: System prompt (for fingerprinting)
            note: Optional note
            error: Optional error message
        """
        if not self._enabled:
            return
        
        self._seq += 1
        
        event = CacheTraceEvent(
            ts=datetime.now(timezone.utc).isoformat(),
            seq=self._seq,
            stage=stage,
            run_id=self._run_id,
            session_id=self._session_id,
            provider=self._provider,
            model_id=self._model_id,
            note=note,
            error=error
        )
        
        if messages:
            event.message_count = len(messages)
            event.message_roles = [
                msg.get("role") if isinstance(msg, dict) else None
                for msg in messages
            ]
            event.message_fingerprints = [
                self._fingerprint_message(msg) for msg in messages
            ]
            event.messages_digest = self._digest_messages(messages)
        
        if system:
            event.system_digest = self._digest_text(system)
        
        self._write_event(event)
    
    def _fingerprint_message(self, msg: Dict[str, Any]) -> str:
        """Generate a short fingerprint for a message."""
        role = msg.get("role", "?") if isinstance(msg, dict) else "?"
        content = msg.get("content", "") if isinstance(msg, dict) else ""
        
        if isinstance(content, str):
            text = content[:100]
        elif isinstance(content, list):
            text = str(len(content)) + " blocks"
        else:
            text = str(type(content))
        
        return f"{role}:{self._short_hash(text)}"
    
    def _digest_messages(self, messages: List[Dict[str, Any]]) -> str:
        """Generate digest of all messages."""
        content = json.dumps(messages, sort_keys=True, default=str)
        return self._short_hash(content)
    
    def _digest_text(self, text: str) -> str:
        """Generate digest of text."""
        return self._short_hash(text)
    
    def _short_hash(self, text: str) -> str:
        """Generate short SHA1 hash."""
        return hashlib.sha1(text.encode()).hexdigest()[:12]
    
    def _write_event(self, event: CacheTraceEvent) -> None:
        """Write event to log file."""
        if not self._file_path:
            return
        
        try:
            # Ensure directory exists
            path = Path(self._file_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            
            # Append event as JSON line
            event_dict = {k: v for k, v in asdict(event).items() if v is not None}
            line = json.dumps(event_dict) + "\n"
            
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
                
        except Exception as e:
            logger.warning(f"Failed to write cache trace: {e}")


def create_cache_trace(
    run_id: Optional[str] = None,
    session_id: Optional[str] = None,
    provider: Optional[str] = None,
    model_id: Optional[str] = None
) -> CacheTrace:
    """Factory function to create cache trace from environment."""
    return CacheTrace.from_env(
        run_id=run_id,
        session_id=session_id,
        provider=provider,
        model_id=model_id
    )
