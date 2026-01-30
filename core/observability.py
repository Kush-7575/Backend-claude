"""
Observability - Structured Logging and Metrics

Provides:
1. Structured JSON logging
2. Request tracing
3. Performance metrics
4. Error tracking
"""
import logging
import time
import json
from typing import Any, Dict, Optional, Callable
from functools import wraps
from contextlib import contextmanager
from datetime import datetime, timezone
from dataclasses import dataclass, field
import traceback

from core.config import settings


class JSONFormatter(logging.Formatter):
    """JSON log formatter for structured logging."""
    
    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        
        # Add extra fields
        if hasattr(record, "user_id"):
            log_data["user_id"] = record.user_id
        if hasattr(record, "session_id"):
            log_data["session_id"] = record.session_id
        if hasattr(record, "request_id"):
            log_data["request_id"] = record.request_id
        if hasattr(record, "duration_ms"):
            log_data["duration_ms"] = record.duration_ms
        if hasattr(record, "extra"):
            log_data.update(record.extra)
        
        # Add exception info
        if record.exc_info:
            log_data["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else None,
                "message": str(record.exc_info[1]) if record.exc_info[1] else None,
                "traceback": traceback.format_exception(*record.exc_info)
            }
        
        return json.dumps(log_data)


def configure_logging(json_format: bool = False):
    """Configure application logging."""
    level = getattr(logging, settings.LOG_LEVEL)
    
    if json_format:
        handler = logging.StreamHandler()
        handler.setFormatter(JSONFormatter())
        logging.root.handlers = [handler]
    
    logging.root.setLevel(level)


@dataclass
class Metrics:
    """Simple in-memory metrics collection."""
    
    counters: Dict[str, int] = field(default_factory=dict)
    histograms: Dict[str, list] = field(default_factory=dict)
    gauges: Dict[str, float] = field(default_factory=dict)
    
    def increment(self, name: str, value: int = 1, tags: Optional[Dict[str, str]] = None):
        """Increment a counter."""
        key = self._make_key(name, tags)
        self.counters[key] = self.counters.get(key, 0) + value
    
    def record(self, name: str, value: float, tags: Optional[Dict[str, str]] = None):
        """Record a value in a histogram."""
        key = self._make_key(name, tags)
        if key not in self.histograms:
            self.histograms[key] = []
        self.histograms[key].append(value)
        # Keep last 1000 values
        if len(self.histograms[key]) > 1000:
            self.histograms[key] = self.histograms[key][-1000:]
    
    def set_gauge(self, name: str, value: float, tags: Optional[Dict[str, str]] = None):
        """Set a gauge value."""
        key = self._make_key(name, tags)
        self.gauges[key] = value
    
    def _make_key(self, name: str, tags: Optional[Dict[str, str]]) -> str:
        if not tags:
            return name
        tag_str = ",".join(f"{k}={v}" for k, v in sorted(tags.items()))
        return f"{name}{{{tag_str}}}"
    
    def get_summary(self) -> Dict[str, Any]:
        """Get metrics summary."""
        summary = {
            "counters": dict(self.counters),
            "gauges": dict(self.gauges),
            "histograms": {}
        }
        
        for name, values in self.histograms.items():
            if values:
                sorted_vals = sorted(values)
                summary["histograms"][name] = {
                    "count": len(values),
                    "min": min(values),
                    "max": max(values),
                    "avg": sum(values) / len(values),
                    "p50": sorted_vals[len(values) // 2],
                    "p95": sorted_vals[int(len(values) * 0.95)] if len(values) >= 20 else None,
                    "p99": sorted_vals[int(len(values) * 0.99)] if len(values) >= 100 else None,
                }
        
        return summary


# Global metrics instance
metrics = Metrics()


@contextmanager
def timed(name: str, tags: Optional[Dict[str, str]] = None):
    """Context manager for timing code blocks."""
    start = time.perf_counter()
    try:
        yield
    finally:
        duration_ms = (time.perf_counter() - start) * 1000
        metrics.record(f"{name}_duration_ms", duration_ms, tags)


def traced(name: Optional[str] = None):
    """Decorator for tracing function execution."""
    def decorator(func: Callable) -> Callable:
        metric_name = name or func.__name__
        
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            start = time.perf_counter()
            metrics.increment(f"{metric_name}_calls")
            
            try:
                result = await func(*args, **kwargs)
                metrics.increment(f"{metric_name}_success")
                return result
            except Exception as e:
                metrics.increment(f"{metric_name}_errors", tags={"error": type(e).__name__})
                raise
            finally:
                duration_ms = (time.perf_counter() - start) * 1000
                metrics.record(f"{metric_name}_duration_ms", duration_ms)
        
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            start = time.perf_counter()
            metrics.increment(f"{metric_name}_calls")
            
            try:
                result = func(*args, **kwargs)
                metrics.increment(f"{metric_name}_success")
                return result
            except Exception as e:
                metrics.increment(f"{metric_name}_errors", tags={"error": type(e).__name__})
                raise
            finally:
                duration_ms = (time.perf_counter() - start) * 1000
                metrics.record(f"{metric_name}_duration_ms", duration_ms)
        
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper
    
    return decorator


class ErrorTracker:
    """Tracks and aggregates errors."""
    
    def __init__(self, max_errors: int = 100):
        self._errors: list = []
        self._max_errors = max_errors
    
    def track(
        self,
        error: Exception,
        context: Optional[Dict[str, Any]] = None
    ):
        """Track an error."""
        error_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": type(error).__name__,
            "message": str(error),
            "traceback": traceback.format_exc(),
            "context": context or {}
        }
        
        self._errors.append(error_data)
        
        # Keep only recent errors
        if len(self._errors) > self._max_errors:
            self._errors = self._errors[-self._max_errors:]
        
        # Increment error counter
        metrics.increment("errors_total", tags={"type": type(error).__name__})
    
    def get_recent(self, limit: int = 10) -> list:
        """Get recent errors."""
        return self._errors[-limit:]
    
    def get_summary(self) -> Dict[str, Any]:
        """Get error summary."""
        by_type: Dict[str, int] = {}
        for err in self._errors:
            err_type = err["type"]
            by_type[err_type] = by_type.get(err_type, 0) + 1
        
        return {
            "total": len(self._errors),
            "by_type": by_type
        }


# Global error tracker
error_tracker = ErrorTracker()
