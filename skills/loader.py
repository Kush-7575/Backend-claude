"""
Skill Loader - SKILL.md Parser and Manager

Implements Clawdbot's skill system:
1. Parses SKILL.md files (YAML frontmatter + markdown)
2. Discovers skills from directory
3. Loads skill instructions into prompts
4. Tracks active skills per user

Skills are self-contained capabilities that can be
added or removed without changing core code.
"""
import os
import logging
from typing import Optional, List, Dict, Any
from pathlib import Path
import yaml
import re

from core.config import settings

logger = logging.getLogger("brainmap.skills")


class Skill:
    """A loaded skill."""
    
    def __init__(
        self,
        name: str,
        description: str,
        instructions: str,
        path: Path,
        metadata: Dict[str, Any]
    ):
        self.name = name
        self.description = description
        self.instructions = instructions
        self.path = path
        self.metadata = metadata
        self.enabled = True
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict."""
        return {
            "name": self.name,
            "description": self.description,
            "instructions": self.instructions,
            "path": str(self.path),
            "enabled": self.enabled
        }


class SkillLoader:
    """
    Loads and manages skills from SKILL.md files.
    
    Skill file format:
    ```
    ---
    name: skill-name
    description: Short description
    triggers:
      - trigger phrase 1
      - trigger phrase 2
    ---
    
    # Instructions
    
    Detailed instructions for the AI when using this skill.
    ```
    """
    
    def __init__(self, skills_dir: Optional[str] = None):
        """
        Initialize skill loader.
        
        Args:
            skills_dir: Directory containing skill folders
        """
        self._skills_dir = Path(skills_dir) if skills_dir else Path("./skills")
        self._skills: Dict[str, Skill] = {}
        self._user_skills: Dict[str, List[str]] = {}  # user_id -> skill names
    
    async def load_all(self) -> int:
        """
        Load all skills from skills directory.
        
        Returns:
            Number of skills loaded
        """
        if not self._skills_dir.exists():
            logger.warning(f"Skills directory not found: {self._skills_dir}")
            return 0
        
        count = 0
        
        for skill_path in self._skills_dir.iterdir():
            if skill_path.is_dir():
                skill_file = skill_path / "SKILL.md"
                if skill_file.exists():
                    try:
                        skill = self._parse_skill_file(skill_file)
                        if skill:
                            self._skills[skill.name] = skill
                            count += 1
                            logger.debug(f"Loaded skill: {skill.name}")
                    except Exception as e:
                        logger.error(f"Failed to load skill {skill_path.name}: {e}")
        
        logger.info(f"Loaded {count} skills from {self._skills_dir}")
        return count
    
    def _parse_skill_file(self, path: Path) -> Optional[Skill]:
        """Parse a SKILL.md file."""
        content = path.read_text(encoding="utf-8")
        
        # Extract YAML frontmatter
        frontmatter_match = re.match(
            r'^---\s*\n(.*?)\n---\s*\n(.*)$',
            content,
            re.DOTALL
        )
        
        if not frontmatter_match:
            logger.warning(f"No frontmatter in {path}")
            # Treat entire content as instructions
            return Skill(
                name=path.parent.name,
                description="",
                instructions=content,
                path=path,
                metadata={}
            )
        
        # Parse YAML
        yaml_content = frontmatter_match.group(1)
        instructions = frontmatter_match.group(2).strip()
        
        try:
            metadata = yaml.safe_load(yaml_content)
        except yaml.YAMLError as e:
            logger.error(f"Invalid YAML in {path}: {e}")
            metadata = {}
        
        return Skill(
            name=metadata.get("name", path.parent.name),
            description=metadata.get("description", ""),
            instructions=instructions,
            path=path,
            metadata=metadata
        )
    
    def get_skill(self, name: str) -> Optional[Skill]:
        """Get a skill by name."""
        return self._skills.get(name)
    
    def list_skills(self) -> List[Dict[str, Any]]:
        """List all available skills."""
        return [skill.to_dict() for skill in self._skills.values()]
    
    async def get_active_skills(self, user_id: str) -> List[Dict[str, str]]:
        """
        Get active skills for a user (for prompt building).
        
        Returns list of {name, instructions} dicts.
        """
        # For now, return all enabled skills
        # TODO: User-specific skill activation
        return [
            {"name": s.name, "instructions": s.instructions}
            for s in self._skills.values()
            if s.enabled
        ]
    
    def enable_skill(self, name: str, user_id: Optional[str] = None):
        """Enable a skill globally or for a user."""
        skill = self._skills.get(name)
        if skill:
            skill.enabled = True
    
    def disable_skill(self, name: str, user_id: Optional[str] = None):
        """Disable a skill globally or for a user."""
        skill = self._skills.get(name)
        if skill:
            skill.enabled = False


# Singleton
_loader: Optional[SkillLoader] = None


def get_skill_loader(skills_dir: Optional[str] = None) -> SkillLoader:
    """Get singleton SkillLoader instance."""
    global _loader
    if _loader is None:
        _loader = SkillLoader(skills_dir)
    return _loader
