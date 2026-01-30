"""Database module exports."""
from database.client import get_client, table, health_check, is_dev_mode

# Alias for compatibility with vector_db
get_supabase = get_client

__all__ = ["get_client", "get_supabase", "table", "health_check", "is_dev_mode"]

