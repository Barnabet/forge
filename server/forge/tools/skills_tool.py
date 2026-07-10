from __future__ import annotations

from pathlib import Path

from forge.engine.skills import discover_skills, parse_skill_md
from forge.tools.base import Tool, ToolContext, ToolResult


class LoadSkillTool(Tool):
    name = "load_skill"
    description = "Load the full instructions of a skill by name."
    params = {"type": "object", "properties": {"name": {"type": "string"}},
              "required": ["name"]}
    read_only = True

    def __init__(self, dirs: list[Path]):
        self.dirs = dirs

    def display(self, args: dict) -> str:
        return args.get("name", self.name)

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        for s in discover_skills(self.dirs):
            if s.name == args["name"]:
                d = Path(s.path)
                _, body = parse_skill_md((d / "SKILL.md").read_text())
                extras = sorted(p.name for p in d.iterdir() if p.name != "SKILL.md")
                files = f"\n\nBundled files in {d}: {', '.join(extras)}" if extras else ""
                return ToolResult(output=body + files)
        return ToolResult(output=f"No skill named {args['name']!r}", is_error=True)
