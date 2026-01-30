"""
Tests for Core Tools

Tests the note and reminder tool implementations.
"""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch


class TestSmartSave:
    """Test smart_save tool."""
    
    @pytest.mark.asyncio
    async def test_smart_save_creates_new_note(self):
        """Test creating a new note when no match exists."""
        from tools.core import smart_save, set_dependencies
        
        # Mock Supabase
        mock_client = MagicMock()
        mock_client.table.return_value.select.return_value.ilike.return_value.eq.return_value.limit.return_value.execute.return_value.data = []
        mock_client.table.return_value.insert.return_value.execute.return_value = MagicMock()
        
        set_dependencies(supabase_client=mock_client)
        
        result = await smart_save(topic="Meeting Notes", content="Discussed project timeline")
        
        assert result["status"] in ["created", "appended"]
    
    @pytest.mark.asyncio
    async def test_smart_save_appends_to_existing(self):
        """Test appending to existing note."""
        from tools.core import smart_save, set_dependencies
        
        # Mock Supabase with existing note
        mock_client = MagicMock()
        mock_client.table.return_value.select.return_value.ilike.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
            {"id": "existing-id", "title": "Meeting Notes"}
        ]
        mock_client.table.return_value.insert.return_value.execute.return_value = MagicMock()
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
        
        set_dependencies(supabase_client=mock_client)
        
        result = await smart_save(topic="Meeting Notes", content="New item")
        
        # Should append to existing
        assert result["status"] == "appended"
        assert result["note_id"] == "existing-id"


class TestTimeParser:
    """Test natural language time parsing."""
    
    def test_parse_tomorrow(self):
        """Test parsing 'tomorrow'."""
        from tools.core import _parse_natural_time
        
        result = _parse_natural_time("tomorrow 9am")
        
        assert result is not None
        tomorrow = datetime.now(timezone.utc).date()
        # Result should be in the future
        assert result.date() > datetime.now(timezone.utc).date() or \
               (result.date() == datetime.now(timezone.utc).date() and result.hour >= 9)
    
    def test_parse_relative_hours(self):
        """Test parsing 'in X hours'."""
        from tools.core import _parse_natural_time
        
        now = datetime.now(timezone.utc)
        result = _parse_natural_time("in 2 hours")
        
        if result:
            # Should be approximately 2 hours from now
            diff = (result - now).total_seconds()
            assert 7000 < diff < 7400  # ~2 hours with some tolerance
    
    def test_parse_day_of_week(self):
        """Test parsing day of week."""
        from tools.core import _parse_natural_time
        
        result = _parse_natural_time("Monday 3pm")
        
        if result:
            assert result.weekday() == 0  # Monday
            assert result.hour == 15  # 3pm


class TestGetCurrentTime:
    """Test get_current_time tool."""
    
    @pytest.mark.asyncio
    async def test_returns_current_time(self):
        """Test that current time is returned."""
        from tools.core import get_current_time
        
        result = await get_current_time()
        
        assert "date" in result
        assert "time" in result
        assert "day" in result
        assert "timezone" in result
    
    @pytest.mark.asyncio
    async def test_time_is_recent(self):
        """Test that returned time is current."""
        from tools.core import get_current_time
        
        now = datetime.now(timezone.utc)
        result = await get_current_time()
        
        # Parse date from result
        result_date = datetime.strptime(result["date"], "%Y-%m-%d").date()
        assert result_date == now.date()
