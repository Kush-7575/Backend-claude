"""Test configuration."""
import pytest
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def mock_supabase():
    """Mock Supabase client."""
    from unittest.mock import MagicMock
    
    client = MagicMock()
    client.table.return_value.select.return_value.execute.return_value.data = []
    client.table.return_value.insert.return_value.execute.return_value = MagicMock()
    client.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
    
    return client


@pytest.fixture
def sample_messages():
    """Sample messages for testing."""
    return [
        {"role": "user", "content": "Hello, assistant!"},
        {"role": "assistant", "content": "Hello! How can I help you today?"},
        {"role": "user", "content": "I need to save a note about my project."},
        {"role": "assistant", "content": "I'd be happy to help you save a note. What would you like to save?"},
    ]


@pytest.fixture
def sample_session():
    """Sample session for testing."""
    from core.session import Session
    
    session = Session(id="test-session", user_id="test-user")
    session.add_message("user", "Hello")
    session.add_message("assistant", "Hi there!")
    
    return session
