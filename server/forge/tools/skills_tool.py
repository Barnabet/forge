from __future__ import annotations

from pathlib import Path

from forge.engine.skills import discover_skills, load_skill_body
from forge.tools.base import Tool, ToolContext, ToolResult


class LoadSkillTool(Tool):
    name = "load_skill"
    description = "Load the full instructions of a skill by name."
    params = {"type": "object", "properties": {"name": {"type": "string"}},
              "required": ["name"]}
    read_only = True

    def __init__(self, dirs: list[Path],
                 tool_descriptions: dict[str, str] | None = None):
        self.dirs = dirs
        # name → description for tools a skill may activate; populated by the
        # actor once the tool set is built so the activation report is complete.
        self.tool_descriptions = tool_descriptions if tool_descriptions is not None else {}

    def display(self, args: dict) -> str:
        return args.get("name", self.name)

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        body = load_skill_body(self.dirs, args["name"])
        if body is None:
            return ToolResult(output=f"No skill named {args['name']!r}", is_error=True)
        activated = ""
        for s in discover_skills(self.dirs):
            if s.name == args["name"] and s.activates:
                lines = [f"- {t} — {self.tool_descriptions.get(t, '')}".rstrip(" —")
                         for t in s.activates]
                activated = ("\n\nActivated tools (now available):\n"
                             + "\n".join(lines))
        return ToolResult(output=body + activated)
