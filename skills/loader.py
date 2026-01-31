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
import sys
import logging
from typing import Optional, List, Dict, Any
from pathlib import Path
import yaml
import re
import shutil

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

    def prompt_description(self) -> str:
        """Get a compact description for prompt listings."""
        if self.description:
            return self.description.strip()
        # Fallback: use first non-empty line from instructions.
        for line in self.instructions.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                return stripped[:120]
        return "No description"

    def requirements(self) -> Dict[str, Any]:
        """Extract eligibility requirements from metadata."""
        requires = self.metadata.get("requires", {}) if isinstance(self.metadata, dict) else {}
        return requires if isinstance(requires, dict) else {}

    def triggers(self) -> List[str]:
        """Extract trigger phrases from metadata."""
        if not isinstance(self.metadata, dict):
            return []
        triggers = self.metadata.get("triggers", [])
        if isinstance(triggers, list):
            return [str(t) for t in triggers if str(t).strip()]
        return []


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
        self._snapshot_version = 0
        self._skill_mtimes: Dict[str, float] = {}
    
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

        self._snapshot_version = int(max(self._snapshot_version + 1, int(self._get_latest_mtime())))
        self._skill_mtimes = self._collect_skill_mtimes()
        
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
            metadata = yaml.safe_load(yaml_content) or {}
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

    def list_available_skills(self, tool_registry=None, query: Optional[str] = None) -> List[Dict[str, str]]:
        """List skill entries for prompt injection."""
        self._maybe_reload()
        items = []
        for skill in self._skills.values():
            if not skill.enabled:
                continue
            if not self._is_skill_eligible(skill, tool_registry):
                continue
            items.append({
                "name": skill.name,
                "description": skill.prompt_description(),
                "location": str(skill.path),
            })
        if query:
            ranked = []
            for entry in items:
                skill = self._skills.get(entry["name"])
                score = self._score_skill(skill, query) if skill else 0
                ranked.append((score, entry))
            ranked.sort(key=lambda x: (x[0], x[1]["name"]), reverse=True)
            return [e for _, e in ranked]
        return items

    def _score_skill(self, skill: Skill, query: str) -> int:
        """Score skill relevance based on trigger phrase matches."""
        if not skill:
            return 0
        text = (query or "").lower()
        score = 0
        for trigger in skill.triggers():
            t = trigger.lower().strip()
            if not t:
                continue
            if t in text:
                score += 2
            else:
                parts = [p for p in t.split() if p]
                if parts and all(p in text for p in parts):
                    score += 1
        return score

    def get_snapshot_version(self) -> int:
        """Get current skills snapshot version."""
        self._maybe_reload()
        return self._snapshot_version

    def _maybe_reload(self) -> None:
        """Reload skills if any SKILL.md changed."""
        current = self._collect_skill_mtimes()
        if not self._skill_mtimes:
            return
        if current != self._skill_mtimes:
            logger.info("Skills changed on disk; reloading.")
            # Best-effort reload without async context
            for k in list(self._skills.keys()):
                self._skills.pop(k, None)
            try:
                import asyncio
                if asyncio.get_event_loop().is_running():
                    asyncio.create_task(self.load_all())
                else:
                    asyncio.run(self.load_all())
            except Exception as e:
                logger.warning(f"Skills reload failed: {e}")

    def _collect_skill_mtimes(self) -> Dict[str, float]:
        mtimes: Dict[str, float] = {}
        if not self._skills_dir.exists():
            return mtimes
        for skill_path in self._skills_dir.glob("**/SKILL.md"):
            try:
                mtimes[str(skill_path)] = skill_path.stat().st_mtime
            except Exception:
                continue
        return mtimes

    def _get_latest_mtime(self) -> float:
        mtimes = self._collect_skill_mtimes()
        return max(mtimes.values(), default=0.0)

    def _is_skill_eligible(self, skill: Skill, tool_registry=None) -> bool:
        requires = skill.requirements()
        # OS filtering
        os_list = requires.get("os")
        if isinstance(os_list, list) and os_list:
            platform = sys.platform.lower()
            normalized = []
            for entry in os_list:
                val = str(entry).lower()
                if val == "windows":
                    val = "win32"
                elif val == "mac" or val == "darwin":
                    val = "darwin"
                normalized.append(val)
            if platform not in normalized:
                return False

        # Env vars
        env_list = requires.get("env")
        if isinstance(env_list, list) and env_list:
            for env_name in env_list:
                if not os.environ.get(str(env_name)):
                    return False

        # Required tools
        tools_list = requires.get("tools")
        if isinstance(tools_list, list) and tools_list and tool_registry:
            available = set(tool_registry.get_tool_names())
            for tool in tools_list:
                if str(tool) not in available:
                    return False

        # Required binaries
        bins_list = requires.get("bins")
        if isinstance(bins_list, list) and bins_list:
            for bin_name in bins_list:
                if not shutil.which(str(bin_name)):
                    return False

        return True
    
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
