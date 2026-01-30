"""Tools module exports."""
from tools.registry import ToolRegistry, ToolDefinition, get_tool_registry, tool
from tools.policy import ToolPolicy, get_tool_policy

# Import memory tools to register them with the registry
from tools.memory_tools import (
    memory_search,
    memory_get,
    memory_list,
    set_memory_dependencies,
    MEMORY_TOOLS
)

# Import HTTP tool for external API calls (Notion, etc.)
from tools.http import http_request

# Import web tools for search and fetch
from tools.web import web_search, web_fetch, set_perplexity_key, is_available as web_available

# Import Notion tools (dedicated, simpler than http_request)
from tools.notion import (
    notion_search,
    notion_create_page,
    notion_append,
    notion_create_database,
    notion_add_row,
    notion_delete_block,
    is_notion_available
)

__all__ = [
    "ToolRegistry",
    "ToolDefinition",
    "get_tool_registry",
    "tool",
    "ToolPolicy",
    "get_tool_policy",
    # Memory tools
    "memory_search",
    "memory_get",
    "memory_list",
    "set_memory_dependencies",
    "MEMORY_TOOLS",
    # HTTP tool
    "http_request",
    # Web tools
    "web_search",
    "web_fetch",
    "set_perplexity_key",
    "web_available",
    # Notion tools
    "notion_search",
    "notion_create_page",
    "notion_append",
    "notion_create_database",
    "notion_add_row",
    "notion_delete_block",
    "is_notion_available",
]
