"""
Tests for Tool Registry

Tests tool registration, execution, and hooks.
"""
import pytest
from tools.registry import ToolRegistry, tool, get_tool_registry


class TestToolRegistry:
    """Test tool registry functionality."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.registry = ToolRegistry()
    
    def test_register_tool_with_decorator(self):
        """Test registering a tool with decorator."""
        @self.registry.register(description="Test tool")
        def my_tool(param: str) -> dict:
            return {"result": param}
        
        assert "my_tool" in self.registry._tools
        assert self.registry._tools["my_tool"].description == "Test tool"
    
    def test_register_tool_with_custom_name(self):
        """Test registering with custom name."""
        @self.registry.register(name="custom_name", description="Custom tool")
        def some_function():
            pass
        
        assert "custom_name" in self.registry._tools
        assert "some_function" not in self.registry._tools
    
    def test_add_tool_directly(self):
        """Test adding tool without decorator."""
        async def handler(x: int) -> dict:
            return {"squared": x * x}
        
        self.registry.add_tool(
            name="square",
            handler=handler,
            description="Square a number",
            parameters={"x": {"type": "integer"}},
            required=["x"]
        )
        
        assert "square" in self.registry._tools
    
    def test_get_tool_definitions(self):
        """Test getting tool definitions for API."""
        @self.registry.register(description="Test")
        def test_tool(a: str, b: int = 0):
            pass
        
        definitions = self.registry.get_tool_definitions()
        
        assert len(definitions) == 1
        assert definitions[0]["name"] == "test_tool"
        assert "input_schema" in definitions[0]
    
    @pytest.mark.asyncio
    async def test_execute_tool_success(self):
        """Test successful tool execution."""
        @self.registry.register(description="Add numbers")
        async def add(a: int, b: int) -> dict:
            return {"sum": a + b}
        
        result = await self.registry.execute("add", {"a": 2, "b": 3})
        
        assert result["status"] == "success"
        assert result["result"]["sum"] == 5
    
    @pytest.mark.asyncio
    async def test_execute_unknown_tool(self):
        """Test executing unknown tool returns error."""
        result = await self.registry.execute("unknown", {})
        
        assert result["status"] == "error"
        assert "Unknown tool" in result["error"]
    
    @pytest.mark.asyncio
    async def test_execute_with_missing_required(self):
        """Test missing required parameter returns error."""
        @self.registry.register(description="Needs param")
        def needs_param(required_param: str):
            pass
        
        result = await self.registry.execute("needs_param", {})
        
        assert result["status"] == "error"
        assert "required" in result["error"].lower()
    
    def test_pre_hook_called(self):
        """Test pre-execution hook is called."""
        hook_called = []
        
        def pre_hook(name, inputs, context):
            hook_called.append(name)
            return None
        
        self.registry.add_pre_hook(pre_hook)
        
        @self.registry.register(description="Test")
        def hooked_tool():
            return {}
        
        import asyncio
        asyncio.run(self.registry.execute("hooked_tool", {}))
        
        assert "hooked_tool" in hook_called


class TestToolDecorator:
    """Test global tool decorator."""
    
    def test_global_tool_decorator(self):
        """Test using global @tool decorator."""
        # Note: This uses the global registry
        @tool(name="global_test", description="Global test tool")
        def global_test_func():
            pass
        
        registry = get_tool_registry()
        assert "global_test" in registry._tools
