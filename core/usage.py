"""
Usage Tracker - Cost and Token Tracking

Tracks API usage across sessions for:
1. Token counts (input/output)
2. Cost estimation
3. Rate limiting
4. Per-user quotas
"""
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from collections import defaultdict

from core.config import settings

logger = logging.getLogger("brainmap.usage")


@dataclass
class UsageRecord:
    """Record of API usage."""
    timestamp: datetime
    user_id: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    session_id: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "created_at": self.timestamp.isoformat(),
            "user_id": self.user_id,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "session_id": self.session_id
        }


class UsageTracker:
    """
    Tracks API usage and costs.
    
    Features:
    - Real-time cost tracking
    - Per-user quotas
    - Daily/monthly aggregates
    - Supabase persistence
    """
    
    # Claude pricing (per 1M tokens)
    PRICING = {
        "claude-sonnet-4-20250514": {"input": 3.00, "output": 15.00},
        "claude-3-5-sonnet-20241022": {"input": 3.00, "output": 15.00},
        "claude-3-opus-20240229": {"input": 15.00, "output": 75.00},
        "claude-3-haiku-20240307": {"input": 0.25, "output": 1.25},
    }
    
    # Default quota (USD per day)
    DEFAULT_DAILY_QUOTA = 10.00
    
    def __init__(self, supabase_client=None):
        self._supabase = supabase_client
        self._records: list[UsageRecord] = []
        self._daily_totals: Dict[str, Dict[str, float]] = defaultdict(
            lambda: {"tokens": 0, "cost": 0.0}
        )
    
    def calculate_cost(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int
    ) -> float:
        """Calculate cost in USD for a request."""
        pricing = self.PRICING.get(model, self.PRICING["claude-sonnet-4-20250514"])
        
        input_cost = (input_tokens / 1_000_000) * pricing["input"]
        output_cost = (output_tokens / 1_000_000) * pricing["output"]
        
        return round(input_cost + output_cost, 6)
    
    async def record_usage(
        self,
        user_id: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        session_id: Optional[str] = None
    ) -> UsageRecord:
        """Record API usage."""
        cost = self.calculate_cost(model, input_tokens, output_tokens)
        
        record = UsageRecord(
            timestamp=datetime.now(timezone.utc),
            user_id=user_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            session_id=session_id
        )
        
        self._records.append(record)
        
        # Update daily totals
        date_key = record.timestamp.strftime("%Y-%m-%d")
        user_key = f"{date_key}:{user_id}"
        self._daily_totals[user_key]["tokens"] += input_tokens + output_tokens
        self._daily_totals[user_key]["cost"] += cost
        
        # Persist to Supabase
        if self._supabase:
            try:
                self._supabase.table("usage_logs").insert(record.to_dict()).execute()
            except Exception as e:
                logger.error(f"Failed to persist usage: {e}")
        
        logger.debug(
            f"Usage: {input_tokens}+{output_tokens} tokens, ${cost:.4f} "
            f"(user: {user_id})"
        )
        
        return record
    
    def get_daily_usage(self, user_id: str, date: Optional[datetime] = None) -> Dict[str, Any]:
        """Get usage for a specific day."""
        date = date or datetime.now(timezone.utc)
        date_key = date.strftime("%Y-%m-%d")
        user_key = f"{date_key}:{user_id}"
        
        totals = self._daily_totals.get(user_key, {"tokens": 0, "cost": 0.0})
        
        return {
            "date": date_key,
            "user_id": user_id,
            "total_tokens": totals["tokens"],
            "total_cost_usd": totals["cost"],
            "quota_remaining": max(0, self.DEFAULT_DAILY_QUOTA - totals["cost"])
        }
    
    def check_quota(self, user_id: str) -> bool:
        """Check if user is within quota."""
        usage = self.get_daily_usage(user_id)
        return usage["quota_remaining"] > 0
    
    async def get_monthly_summary(self, user_id: str) -> Dict[str, Any]:
        """Get monthly usage summary."""
        now = datetime.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        
        total_tokens = 0
        total_cost = 0.0
        days_active = 0
        
        for key, totals in self._daily_totals.items():
            date_str, uid = key.split(":", 1)
            if uid != user_id:
                continue
            
            try:
                record_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                if record_date >= month_start:
                    total_tokens += totals["tokens"]
                    total_cost += totals["cost"]
                    days_active += 1
            except ValueError:
                continue
        
        return {
            "month": now.strftime("%Y-%m"),
            "user_id": user_id,
            "total_tokens": total_tokens,
            "total_cost_usd": round(total_cost, 4),
            "days_active": days_active,
            "avg_daily_cost": round(total_cost / max(days_active, 1), 4)
        }


# Singleton
_tracker: Optional[UsageTracker] = None


def get_usage_tracker(supabase_client=None) -> UsageTracker:
    """Get singleton UsageTracker instance."""
    global _tracker
    if _tracker is None:
        _tracker = UsageTracker(supabase_client)
    return _tracker
