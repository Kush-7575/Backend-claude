"""
Tool Registry - Tool Management and Execution

Manages tool definitions and execution with:
1. Tool registration and discovery
2. Pre/post execution hooks
3. Input validation
4. Result formatting
5. Error handling

Based on Clawdbot's tool policy patterns.
"""
import logging
from typing import Dict, Any, List, Optional, Callable, Type
from dataclasses import dataclass, field
from functools import wraps
import inspect
import json

logger = logging.getLogger("brainmap.tools")


@dataclass
class ToolDefinition:
    """Definition of a registered tool."""
    name: str
    description: str
    handler: Callable
    parameters: Dict[str, Any]
    required: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_api_format(self) -> Dict[str, Any]:
        """Convert to Claude API tool format."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": self.parameters,
                "required": self.required
            }
        }


class ToolRegistry:
    """
    Central registry for all tools.
    
    Features:
    - Register tools via decorator or direct registration
    - Pre/post execution hooks
    - Input validation
    - Error handling with consistent format
    """
    
    def __init__(self):
        self._tools: Dict[str, ToolDefinition] = {}
        self._pre_hooks: List[Callable] = []
        self._post_hooks: List[Callable] = []
    
    def register(
        self,
        name: Optional[str] = None,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Callable:
        """
        Decorator to register a tool.
        
        Usage:
            @registry.register(description="Save a note")
            def save_note(title: str, content: str) -> dict:
                ...
        """
        def decorator(func: Callable) -> Callable:
            tool_name = name or func.__name__
            tool_desc = description or func.__doc__ or f"Execute {tool_name}"
            
            # Extract parameters from function signature
            sig = inspect.signature(func)
            params = {}
            required = []
            
            for param_name, param in sig.parameters.items():
                if param_name in ("self", "cls", "uid", "user_id"):
                    continue
                
                param_type = "string"  # Default
                param_desc = ""
                
                # Get type annotation
                if param.annotation != inspect.Parameter.empty:
                    if param.annotation == int:
                        param_type = "integer"
                    elif param.annotation == bool:
                        param_type = "boolean"
                    elif param.annotation == float:
                        param_type = "number"
                    elif param.annotation == list or param.annotation == List:
                        param_type = "array"
                
                params[param_name] = {
                    "type": param_type,
                    "description": param_desc
                }
                
                # Check if required
                if param.default == inspect.Parameter.empty:
                    required.append(param_name)
            
            # Create tool definition
            tool_def = ToolDefinition(
                name=tool_name,
                description=tool_desc.strip(),
                handler=func,
                parameters=params,
                required=required,
                metadata=metadata or {}
            )
            
            self._tools[tool_name] = tool_def
            logger.debug(f"Registered tool: {tool_name}")
            
            return func
        
        return decorator
    
    def add_tool(
        self,
        name: str,
        handler: Callable,
        description: str,
        parameters: Dict[str, Any],
        required: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """Directly register a tool (without decorator)."""
        self._tools[name] = ToolDefinition(
            name=name,
            description=description,
            handler=handler,
            parameters=parameters,
            required=required or [],
            metadata=metadata or {}
        )
        logger.debug(f"Added tool: {name}")
    
    def add_pre_hook(self, hook: Callable) -> None:
        """Add pre-execution hook."""
        self._pre_hooks.append(hook)
    
    def add_post_hook(self, hook: Callable) -> None:
        """Add post-execution hook."""
        self._post_hooks.append(hook)
    
    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """Get all tools in Claude API format."""
        return [tool.to_api_format() for tool in self._tools.values()]
    
    def get_tool(self, name: str) -> Optional[ToolDefinition]:
        """Get a specific tool by name."""
        return self._tools.get(name)
    
    async def execute(
        self,
        name: str,
        inputs: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Execute a tool by name.
        
        Args:
            name: Tool name
            inputs: Tool inputs
            context: Additional context (user_id, session, etc.)
        
        Returns:
            Dict with status, result/error
        """
        tool = self._tools.get(name)
        
        if not tool:
            logger.error(f"Tool not found: {name}")
            return {
                "status": "error",
                "error": f"Unknown tool: {name}"
            }
        
        context = context or {}
        
        try:
            # Run pre-hooks
            for hook in self._pre_hooks:
                hook_result = await self._run_hook(hook, name, inputs, context)
                if hook_result is not None:
                    inputs = hook_result
            
            # Validate inputs
            validation_error = self._validate_inputs(tool, inputs)
            if validation_error:
                return {
                    "status": "error",
                    "error": validation_error
                }
            
            # Execute tool
            logger.info(f"Executing tool: {name}")
            
            if inspect.iscoroutinefunction(tool.handler):
                result = await tool.handler(**inputs)
            else:
                result = tool.handler(**inputs)
            
            # Run post-hooks
            for hook in self._post_hooks:
                hook_result = await self._run_hook(hook, name, result, context)
                if hook_result is not None:
                    result = hook_result
            
            logger.info(f"Tool {name} completed successfully")
            
            return {
                "status": "success",
                "result": result
            }
            
        except Exception as e:
            logger.error(f"Tool {name} failed: {e}")
            return {
                "status": "error",
                "error": str(e)
            }
    
    async def _run_hook(
        self,
        hook: Callable,
        name: str,
        data: Any,
        context: Dict[str, Any]
    ) -> Optional[Any]:
        """Run a hook, handling async if needed."""
        try:
            if inspect.iscoroutinefunction(hook):
                return await hook(name, data, context)
            else:
                return hook(name, data, context)
        except Exception as e:
            logger.warning(f"Hook failed: {e}")
            return None
    
    def _validate_inputs(
        self,
        tool: ToolDefinition,
        inputs: Dict[str, Any]
    ) -> Optional[str]:
        """Validate tool inputs against schema."""
        # Check required fields
        for req in tool.required:
            if req not in inputs:
                return f"Missing required parameter: {req}"
        
        # Type validation could be added here
        return None


# Singleton
_registry: Optional[ToolRegistry] = None


def get_tool_registry() -> ToolRegistry:
    """Get singleton ToolRegistry instance."""
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
    return _registry


# Convenience decorator
def tool(
    name: Optional[str] = None,
    description: Optional[str] = None
) -> Callable:
    """
    Global tool decorator.
    
    Usage:
        @tool(description="Save content to notes")
        def save_note(title: str, content: str):
            ...
    """
    return get_tool_registry().register(name=name, description=description)
