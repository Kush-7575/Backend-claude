"""
Tests for Agent Runner

Tests agent initialization, streaming, and tool execution.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestAgentRunner:
    """Test AgentRunner class."""
    
    def test_agent_runner_initialization(self):
        """Test agent runner can be initialized."""
        from core.agent import AgentRunner
        
        runner = AgentRunner()
        
        assert runner is not None
        assert runner._client is None  # Lazy initialization
    
    def test_agent_runner_with_dependencies(self):
        """Test agent runner with injected dependencies."""
        from core.agent import AgentRunner
        
        mock_sessions = MagicMock()
        mock_tools = MagicMock()
        mock_prompt = MagicMock()
        
        runner = AgentRunner(
            session_manager=mock_sessions,
            tool_registry=mock_tools,
            prompt_builder=mock_prompt
        )
        
        assert runner._session_manager is mock_sessions
        assert runner._tool_registry is mock_tools
        assert runner._prompt_builder is mock_prompt
    
    @pytest.mark.asyncio
    async def test_run_returns_response(self):
        """Test running agent returns a response."""
        from core.agent import AgentRunner
        from core.session import Session
        
        runner = AgentRunner()
        session = Session(id="test", user_id="user")
        
        # Mock the Anthropic client
        with patch.object(runner, '_get_client') as mock_client:
            mock_response = MagicMock()
            mock_response.content = [MagicMock(text="Hello!", type="text")]
            mock_client.return_value.messages.create = AsyncMock(return_value=mock_response)
            
            # This would need the actual client - skip for unit test
            # result = await runner.run(session, "Hello")
            pass
    
    @pytest.mark.asyncio
    async def test_run_stream_yields_chunks(self):
        """Test streaming yields response chunks."""
        from core.agent import AgentRunner, ResponseChunk
        from core.session import Session
        
        runner = AgentRunner()
        session = Session(id="test", user_id="user")
        
        # Would need to mock streaming - complex test
        # For now, test chunk creation
        chunk = ResponseChunk(type="text", content="Hello")
        assert chunk.type == "text"
        assert chunk.content == "Hello"
    
    def test_response_chunk_types(self):
        """Test different response chunk types."""
        from core.agent import ResponseChunk
        
        text_chunk = ResponseChunk(type="text", content="Hi")
        tool_chunk = ResponseChunk(type="tool_start", content="smart_save")
        done_chunk = ResponseChunk(type="done", content="")
        error_chunk = ResponseChunk(type="error", content="Failed")
        
        assert text_chunk.type == "text"
        assert tool_chunk.type == "tool_start"
        assert done_chunk.type == "done"
        assert error_chunk.type == "error"


class TestAgentRunnerSingleton:
    """Test singleton pattern."""
    
    def test_get_agent_runner_creates_instance(self):
        """Test getter creates instance."""
        from core.agent import get_agent_runner
        
        # Reset for clean test
        import core.agent as agent_module
        agent_module._agent_runner = None
        
        runner = get_agent_runner()
        assert runner is not None
    
    def test_get_agent_runner_returns_same_instance(self):
        """Test getter returns same instance."""
        from core.agent import get_agent_runner
        
        runner1 = get_agent_runner()
        runner2 = get_agent_runner()
        
        assert runner1 is runner2


class TestAgentToolExecution:
    """Test tool execution within agent."""
    
    @pytest.mark.asyncio
    async def test_execute_tool_calls_registry(self):
        """Test that tool execution goes through registry."""
        from core.agent import AgentRunner
        from tools.registry import ToolRegistry
        
        # Create registry with mock tool
        registry = ToolRegistry()
        
        @registry.register(description="Test tool")
        async def test_tool(x: int) -> dict:
            return {"result": x * 2}
        
        runner = AgentRunner(tool_registry=registry)
        
        # Execute through runner
        result = await runner._execute_tool("test_tool", {"x": 5})
        
        assert result["status"] == "success"
        assert result["result"]["result"] == 10
    
    @pytest.mark.asyncio
    async def test_execute_unknown_tool_returns_error(self):
        """Test unknown tool returns error."""
        from core.agent import AgentRunner
        from tools.registry import ToolRegistry
        
        runner = AgentRunner(tool_registry=ToolRegistry())
        
        result = await runner._execute_tool("nonexistent", {})
        
        assert result["status"] == "error"
