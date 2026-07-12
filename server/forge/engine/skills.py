from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel


class SkillMeta(BaseModel):
    name: str
    description: str
    path: str  # directory containing SKILL.md
    activates: list[str] = []  # tool names this skill unlocks when loaded


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


def stock_skills_dir() -> Path:
    """Skills bundled with the app (forge/skills/); lowest precedence."""
    return Path(__file__).resolve().parent.parent / "skills"


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
            activates = fm.get("activates_tools") or []
            if not isinstance(activates, list):
                activates = []
            activates = [t for t in activates if isinstance(t, str)]
            found[name] = SkillMeta(
                name=name, description=fm.get("description", ""), path=str(d),
                activates=activates)
    return list(found.values())


def load_skill_body(dirs: list[Path], name: str) -> str | None:
    """Return a skill's SKILL.md body (plus a note listing any bundled files),
    or None if no skill by that name is found."""
    for s in discover_skills(dirs):
        if s.name == name:
            d = Path(s.path)
            _, body = parse_skill_md((d / "SKILL.md").read_text())
            extras = sorted(p.name for p in d.iterdir() if p.name != "SKILL.md")
            files = f"\n\nBundled files in {d}: {', '.join(extras)}" if extras else ""
            return body + files
    return None


def skill_tool_activations(dirs: list[Path]) -> dict[str, str]:
    """Map each gated tool name → the skill name that activates it."""
    activations: dict[str, str] = {}
    for s in discover_skills(dirs):
        for tool in s.activates:
            activations[tool] = s.name
    return activations
