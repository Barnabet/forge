from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel


class SkillMeta(BaseModel):
    name: str
    description: str
    path: str  # directory containing SKILL.md


def parse_skill_md(text: str) -> tuple[dict, str]:
    """Split YAML frontmatter from body. Returns ({}, text) if no frontmatter."""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            fm = yaml.safe_load(parts[1])
            if not isinstance(fm, dict):
                fm = {}
            return fm, parts[2].strip()
    return {}, text.strip()


def discover_skills(dirs: list[Path]) -> list[SkillMeta]:
    found: dict[str, SkillMeta] = {}
    for root in dirs:  # later dirs override earlier
        if not root.is_dir():
            continue
        for d in sorted(root.iterdir()):
            md = d / "SKILL.md"
            if not md.is_file():
                continue
            try:
                fm, _ = parse_skill_md(md.read_text())
            except (yaml.YAMLError, OSError, UnicodeDecodeError):
                continue  # a broken skill must not take down system prompt build
            name = fm.get("name", d.name)
            found[name] = SkillMeta(
                name=name, description=fm.get("description", ""), path=str(d))
    return list(found.values())
