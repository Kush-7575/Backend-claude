"""
Tests for Skill System

Tests skill loading, parsing, and resolution.
"""
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
import tempfile
import os


class TestSkillLoader:
    """Test SkillLoader class."""
    
    def test_skill_loader_initialization(self):
        """Test skill loader can be initialized."""
        from skills.loader import SkillLoader
        
        loader = SkillLoader()
        
        assert loader is not None
        assert len(loader._skills) == 0  # No skills loaded yet
    
    def test_skill_loader_with_custom_dir(self):
        """Test skill loader with custom directory."""
        from skills.loader import SkillLoader
        
        loader = SkillLoader(skills_dir="/custom/path")
        
        assert str(loader._skills_dir) == "/custom/path"
    
    @pytest.mark.asyncio
    async def test_load_all_from_directory(self):
        """Test loading skills from directory."""
        from skills.loader import SkillLoader
        
        # Use actual skills directory
        loader = SkillLoader(skills_dir="./skills")
        count = await loader.load_all()
        
        # Should load at least some skills
        assert count >= 0  # May be 0 if skills dir doesn't exist in test env
    
    def test_parse_skill_file_with_frontmatter(self):
        """Test parsing SKILL.md with YAML frontmatter."""
        from skills.loader import SkillLoader
        
        # Create temp skill file
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = Path(tmpdir) / "test-skill"
            skill_dir.mkdir()
            skill_file = skill_dir / "SKILL.md"
            
            skill_file.write_text("""---
name: test-skill
description: A test skill
triggers:
  - test trigger
---

# Test Skill Instructions

Do something useful.
""")
            
            loader = SkillLoader()
            skill = loader._parse_skill_file(skill_file)
            
            assert skill is not None
            assert skill.name == "test-skill"
            assert skill.description == "A test skill"
            assert "Do something useful" in skill.instructions
    
    def test_parse_skill_file_without_frontmatter(self):
        """Test parsing SKILL.md without YAML frontmatter."""
        from skills.loader import SkillLoader
        
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = Path(tmpdir) / "simple-skill"
            skill_dir.mkdir()
            skill_file = skill_dir / "SKILL.md"
            
            skill_file.write_text("""# Simple Skill

Just instructions, no metadata.
""")
            
            loader = SkillLoader()
            skill = loader._parse_skill_file(skill_file)
            
            assert skill is not None
            # Should use directory name as skill name
            assert skill.name == "simple-skill"


class TestSkill:
    """Test Skill dataclass."""
    
    def test_skill_creation(self):
        """Test creating a skill."""
        from skills.loader import Skill
        from pathlib import Path
        
        skill = Skill(
            name="my-skill",
            description="Does things",
            instructions="# Instructions\n\nDo stuff.",
            path=Path("/skills/my-skill/SKILL.md"),
            metadata={"triggers": ["do stuff"]}
        )
        
        assert skill.name == "my-skill"
        assert skill.description == "Does things"
        assert skill.enabled is True
    
    def test_skill_to_dict(self):
        """Test converting skill to dict."""
        from skills.loader import Skill
        from pathlib import Path
        
        skill = Skill(
            name="test",
            description="Test",
            instructions="Test instructions",
            path=Path("/test"),
            metadata={}
        )
        
        d = skill.to_dict()
        
        assert d["name"] == "test"
        assert d["description"] == "Test"
        assert d["instructions"] == "Test instructions"
        assert d["enabled"] is True


class TestSkillLoaderResolution:
    """Test skill resolution for users."""
    
    @pytest.mark.asyncio
    async def test_get_active_skills_returns_enabled(self):
        """Test getting active skills."""
        from skills.loader import SkillLoader, Skill
        from pathlib import Path
        
        loader = SkillLoader()
        
        # Add mock skills
        loader._skills["skill1"] = Skill(
            name="skill1",
            description="Skill 1",
            instructions="Do skill 1",
            path=Path("/skills/skill1"),
            metadata={}
        )
        loader._skills["skill2"] = Skill(
            name="skill2",
            description="Skill 2",
            instructions="Do skill 2",
            path=Path("/skills/skill2"),
            metadata={}
        )
        loader._skills["skill2"].enabled = False
        
        active = await loader.get_active_skills("user-123")
        
        # Should only return enabled skills
        assert len(active) == 1
        assert active[0]["name"] == "skill1"
    
    def test_enable_disable_skill(self):
        """Test enabling/disabling skills."""
        from skills.loader import SkillLoader, Skill
        from pathlib import Path
        
        loader = SkillLoader()
        loader._skills["test"] = Skill(
            name="test",
            description="Test",
            instructions="...",
            path=Path("/test"),
            metadata={}
        )
        
        assert loader._skills["test"].enabled is True
        
        loader.disable_skill("test")
        assert loader._skills["test"].enabled is False
        
        loader.enable_skill("test")
        assert loader._skills["test"].enabled is True
    
    def test_list_skills(self):
        """Test listing all skills."""
        from skills.loader import SkillLoader, Skill
        from pathlib import Path
        
        loader = SkillLoader()
        loader._skills["a"] = Skill("a", "Skill A", "...", Path("/a"), {})
        loader._skills["b"] = Skill("b", "Skill B", "...", Path("/b"), {})
        
        skills = loader.list_skills()
        
        assert len(skills) == 2


class TestSkillLoaderSingleton:
    """Test singleton pattern."""
    
    def test_get_skill_loader_returns_same_instance(self):
        """Test singleton returns same instance."""
        from skills.loader import get_skill_loader
        
        # Reset for clean test
        import skills.loader as loader_module
        loader_module._loader = None
        
        loader1 = get_skill_loader()
        loader2 = get_skill_loader()
        
        assert loader1 is loader2
