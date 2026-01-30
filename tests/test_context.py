"""
Tests for Context Manager

Tests token counting, compaction detection, and truncation.
"""
import pytest
from core.context import ContextManager, get_context_manager


class TestContextManager:
    """Test context management functionality."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.manager = ContextManager()
    
    def test_count_tokens_simple(self):
        """Test basic token counting."""
        text = "Hello, world!"
        tokens = self.manager.count_tokens(text)
        
        # Should be around 4 tokens
        assert tokens > 0
        assert tokens < 10
    
    def test_count_tokens_empty(self):
        """Test empty string returns 0."""
        assert self.manager.count_tokens("") == 0
        assert self.manager.count_tokens(None) == 0
    
    def test_count_message_tokens(self):
        """Test counting tokens in a message dict."""
        msg = {"role": "user", "content": "What is the weather today?"}
        tokens = self.manager.count_message_tokens(msg)
        
        assert tokens > 0
        assert tokens < 20
    
    def test_needs_compaction_below_threshold(self):
        """Test compaction not needed when below threshold."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"}
        ]
        
        assert not self.manager.needs_compaction(messages)
    
    def test_get_metrics(self):
        """Test metrics calculation."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there! How can I help?"}
        ]
        
        metrics = self.manager.get_metrics(messages)
        
        assert metrics.total_tokens > 0
        assert metrics.message_count == 2
        assert metrics.usage_percent < 1  # Should be very low
        assert not metrics.needs_compaction
    
    def test_truncate_to_fit(self):
        """Test message truncation."""
        # Create many messages
        messages = [
            {"role": "user", "content": f"Message {i}" * 100}
            for i in range(100)
        ]
        
        truncated = self.manager.truncate_to_fit(messages, max_tokens=1000)
        
        # Should have fewer messages
        assert len(truncated) < len(messages)
        # Should keep most recent
        assert truncated[-1] == messages[-1]
    
    def test_estimate_available_tokens(self):
        """Test available token estimation."""
        messages = [{"role": "user", "content": "Short message"}]
        
        available = self.manager.estimate_available_tokens(messages)
        
        # Should be close to max minus reserve
        assert available > 100000  # Most of context available


class TestContextManagerSingleton:
    """Test singleton pattern."""
    
    def test_get_context_manager_returns_same_instance(self):
        """Test singleton returns same instance."""
        manager1 = get_context_manager()
        manager2 = get_context_manager()
        
        assert manager1 is manager2
