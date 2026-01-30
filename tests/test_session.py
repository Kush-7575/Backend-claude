"""
Tests for Session Manager

Tests session creation, loading, and compaction.
"""
import pytest
from datetime import datetime, timezone
from core.session import Session, Message, SessionManager


class TestMessage:
    """Test Message dataclass."""
    
    def test_message_creation(self):
        """Test creating a message."""
        msg = Message(role="user", content="Hello")
        
        assert msg.role == "user"
        assert msg.content == "Hello"
        assert isinstance(msg.timestamp, datetime)
    
    def test_message_to_dict(self):
        """Test converting to API format."""
        msg = Message(role="assistant", content="Hi there!")
        d = msg.to_dict()
        
        assert d == {"role": "assistant", "content": "Hi there!"}
    
    def test_message_to_storage(self):
        """Test converting to storage format."""
        msg = Message(role="user", content="Test", metadata={"key": "value"})
        s = msg.to_storage()
        
        assert s["role"] == "user"
        assert s["content"] == "Test"
        assert "timestamp" in s
        assert s["metadata"] == {"key": "value"}
    
    def test_message_from_storage(self):
        """Test loading from storage format."""
        data = {
            "role": "user",
            "content": "Stored message",
            "timestamp": "2024-01-01T12:00:00+00:00",
            "metadata": {"source": "test"}
        }
        
        msg = Message.from_storage(data)
        
        assert msg.role == "user"
        assert msg.content == "Stored message"
        assert msg.metadata == {"source": "test"}


class TestSession:
    """Test Session dataclass."""
    
    def test_session_creation(self):
        """Test creating a session."""
        session = Session(id="test-123", user_id="user-456")
        
        assert session.id == "test-123"
        assert session.user_id == "user-456"
        assert len(session.messages) == 0
    
    def test_add_message(self):
        """Test adding a message to session."""
        session = Session(id="test", user_id="user")
        
        msg = session.add_message("user", "Hello")
        
        assert len(session.messages) == 1
        assert session.messages[0] is msg
        assert msg.role == "user"
        assert msg.content == "Hello"
    
    def test_get_api_messages(self):
        """Test getting messages in API format."""
        session = Session(id="test", user_id="user")
        session.add_message("user", "Question?")
        session.add_message("assistant", "Answer!")
        
        api_msgs = session.get_api_messages()
        
        assert len(api_msgs) == 2
        assert api_msgs[0] == {"role": "user", "content": "Question?"}
        assert api_msgs[1] == {"role": "assistant", "content": "Answer!"}
    
    def test_to_storage(self):
        """Test converting session to storage format."""
        session = Session(id="test", user_id="user", title="Test Session")
        session.add_message("user", "Hi")
        
        data = session.to_storage()
        
        assert data["id"] == "test"
        assert data["user_id"] == "user"
        assert data["title"] == "Test Session"
        assert len(data["messages"]) == 1


class TestSessionManager:
    """Test SessionManager class."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.manager = SessionManager()
    
    def test_create_session(self):
        """Test creating a new session."""
        session = self.manager.create_session("user-123", title="Test")
        
        assert session.user_id == "user-123"
        assert session.title == "Test"
        assert session.id in self.manager._sessions_cache
    
    @pytest.mark.asyncio
    async def test_add_message_to_session(self):
        """Test adding message through manager."""
        session = self.manager.create_session("user")
        
        msg = await self.manager.add_message(session, "user", "Hello", auto_compact=False)
        
        assert len(session.messages) == 1
        assert msg.content == "Hello"
    
    def test_get_context_metrics(self):
        """Test getting context metrics."""
        session = self.manager.create_session("user")
        session.add_message("user", "Short message")
        
        metrics = self.manager.get_context_metrics(session)
        
        assert metrics.total_tokens > 0
        assert metrics.message_count == 1
