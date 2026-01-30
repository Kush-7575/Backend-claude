"""
Health Router - Health Checks and Status

Provides:
1. /health - Service health status
2. /health/ready - Readiness check
3. /health/live - Liveness check
"""
from fastapi import APIRouter
from datetime import datetime, timezone

from database.client import health_check as db_health_check
from core.config import settings

router = APIRouter()


@router.get("/health")
async def health():
    """
    Full health check.
    
    Returns status of all components.
    """
    db = db_health_check()
    
    return {
        "status": "healthy" if db["connected"] else "degraded",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": "1.0.0",
        "components": {
            "database": db,
            "config": {
                "claude_model": settings.CLAUDE_MODEL,
                "max_context_tokens": settings.MAX_CONTEXT_TOKENS,
                "heartbeat_enabled": settings.HEARTBEAT_ENABLED
            }
        }
    }


@router.get("/health/ready")
async def readiness():
    """
    Readiness probe for Kubernetes/load balancers.
    
    Returns 200 if ready to accept traffic.
    """
    db = db_health_check()
    
    if db["connected"]:
        return {"ready": True}
    else:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="Database not ready")


@router.get("/health/live")
async def liveness():
    """
    Liveness probe for Kubernetes.
    
    Returns 200 if service is alive (even if degraded).
    """
    return {"alive": True}
