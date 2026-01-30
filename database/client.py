"""
Supabase Client - Database Connection

Provides:
1. Singleton Supabase client
2. Health check
3. Table helper
"""
import logging
from typing import Optional
from functools import lru_cache

from supabase import create_client, Client

from core.config import settings

logger = logging.getLogger("brainmap.database")


_client: Optional[Client] = None


def get_client() -> Client:
    """Get or create Supabase client singleton."""
    global _client
    
    if _client is None:
        _client = create_client(
            settings.SUPABASE_URL,
            settings.SUPABASE_KEY
        )
        logger.info("Supabase client initialized")
    
    return _client


def table(name: str):
    """Get a table reference."""
    return get_client().table(name)


def health_check() -> dict:
    """Check database connectivity."""
    try:
        client = get_client()
        # Simple query to test connection
        client.table("notes").select("id").limit(1).execute()
        return {"connected": True, "status": "healthy"}
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        return {"connected": False, "status": "error", "error": str(e)}


def is_dev_mode() -> bool:
    """Check if running in development mode."""
    return settings.DEV_MODE
