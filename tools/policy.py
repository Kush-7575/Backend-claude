"""
Tool Policy - Access Control for Tools

Controls which tools are available based on:
1. Core vs integration tools
2. User's enabled integrations
3. API key availability
4. Per-user allowlists/blocklists

Based on Clawdbot's multi-layer policy system.
"""
import logging
from typing import List, Dict, Set, Optional
from dataclasses import dataclass, field

from core.config import settings

logger = logging.getLogger("brainmap.tools.policy")


@dataclass
class ToolPolicy:
    """
    Tool access policy for different contexts.
    
    Layers (applied in order):
    1. Global defaults (CORE_TOOLS always available)
    2. Integration availability (based on API keys)
    3. User allowlist/blocklist
    """
    
    # Core tools - always available
    CORE_TOOLS: List[str] = field(default_factory=lambda: [
        "smart_save",
        "save_note",
        "search_notes",
        "get_notes",
        "append_to_note",
        "delete_note",
        "create_reminder",
        "get_reminders",
        "complete_reminder",
        "get_current_time",
    ])
    
    # Integration tools - require API keys
    INTEGRATION_TOOLS: Dict[str, List[str]] = field(default_factory=lambda: {
        "notion": [
            "notion_search",
            "notion_create_page",
            "notion_append",
        ],
        "calendar": [
            "calendar_list_events",
            "calendar_create_event",
            "calendar_check_availability",
        ],
        "homekit": [
            "homekit_status",
            "homekit_control",
        ],
    })
    
    # Integration requirements (env vars needed)
    INTEGRATION_REQUIREMENTS: Dict[str, List[str]] = field(default_factory=lambda: {
        "notion": ["NOTION_API_KEY"],
        "calendar": ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"],
        "homekit": ["HOMEKIT_BRIDGE_IP"],
    })
    
    # Per-user overrides (loaded from DB)
    _user_allowlists: Dict[str, Set[str]] = field(default_factory=dict)
    _user_blocklists: Dict[str, Set[str]] = field(default_factory=dict)
    
    def get_available_integrations(self) -> List[str]:
        """Get integrations that have required env vars configured."""
        available = []
        
        for integration, requirements in self.INTEGRATION_REQUIREMENTS.items():
            if all(getattr(settings, req, None) for req in requirements):
                available.append(integration)
        
        return available
    
    def resolve(
        self,
        user_id: str,
        enabled_integrations: Optional[List[str]] = None
    ) -> List[str]:
        """
        Resolve available tools for a user.
        
        Args:
            user_id: User ID
            enabled_integrations: User's enabled integrations (None = check all available)
        
        Returns:
            List of tool names available to this user
        """
        tools = set(self.CORE_TOOLS)
        
        # Get available integrations
        if enabled_integrations is None:
            enabled_integrations = self.get_available_integrations()
        
        # Add integration tools
        for integration in enabled_integrations:
            if integration in self.INTEGRATION_TOOLS:
                # Check if integration is actually configured
                if integration in self.get_available_integrations():
                    tools.update(self.INTEGRATION_TOOLS[integration])
        
        # Apply user allowlist (if set, ONLY these tools allowed)
        if user_id in self._user_allowlists:
            tools = tools.intersection(self._user_allowlists[user_id])
        
        # Apply user blocklist
        if user_id in self._user_blocklists:
            tools = tools - self._user_blocklists[user_id]
        
        return sorted(list(tools))
    
    def set_user_allowlist(self, user_id: str, tools: List[str]):
        """Set explicit allowlist for user (overrides defaults)."""
        self._user_allowlists[user_id] = set(tools)
        logger.info(f"Set allowlist for {user_id}: {tools}")
    
    def set_user_blocklist(self, user_id: str, tools: List[str]):
        """Set blocklist for user (removes from available)."""
        self._user_blocklists[user_id] = set(tools)
        logger.info(f"Set blocklist for {user_id}: {tools}")
    
    def clear_user_overrides(self, user_id: str):
        """Clear all user-specific overrides."""
        self._user_allowlists.pop(user_id, None)
        self._user_blocklists.pop(user_id, None)
    
    def is_tool_allowed(self, user_id: str, tool_name: str) -> bool:
        """Check if a specific tool is allowed for a user."""
        available = self.resolve(user_id)
        return tool_name in available
    
    def get_integration_for_tool(self, tool_name: str) -> Optional[str]:
        """Get which integration a tool belongs to (None if core)."""
        for integration, tools in self.INTEGRATION_TOOLS.items():
            if tool_name in tools:
                return integration
        return None
    
    def explain_unavailable(self, tool_name: str) -> str:
        """Explain why a tool might be unavailable."""
        integration = self.get_integration_for_tool(tool_name)
        
        if integration is None:
            return f"Unknown tool: {tool_name}"
        
        requirements = self.INTEGRATION_REQUIREMENTS.get(integration, [])
        missing = [req for req in requirements if not getattr(settings, req, None)]
        
        if missing:
            return f"{integration.title()} integration requires: {', '.join(missing)}"
        
        return f"Tool {tool_name} is not enabled for this user"


# Singleton instance
_policy: Optional[ToolPolicy] = None


def get_tool_policy() -> ToolPolicy:
    """Get singleton ToolPolicy instance."""
    global _policy
    if _policy is None:
        _policy = ToolPolicy()
    return _policy
