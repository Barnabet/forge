from __future__ import annotations

from pathlib import Path

from forge.tools.base import Tool
from forge.tools.bash import BashTool
from forge.tools.files_read import ReadFileTool
from forge.tools.files_write import EditFileTool, WriteFileTool
from forge.tools.search import GlobTool, GrepTool, ListDirTool
from forge.tools.skills_tool import LoadSkillTool
from forge.tools.subagents import SpawnAgentsTool


def default_tools(skill_dirs: list[Path], subagents: SpawnAgentsTool | None = None) -> dict[str, Tool]:
    tools: list[Tool] = [
        BashTool(), ReadFileTool(), WriteFileTool(), EditFileTool(),
        GlobTool(), GrepTool(), ListDirTool(),
        LoadSkillTool(skill_dirs),
    ]
    if subagents is not None:
        tools.append(subagents)
    return {t.name: t for t in tools}
