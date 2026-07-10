from __future__ import annotations

from pathlib import Path

from forge.tools.base import Tool
from forge.tools.bash import BashTool
from forge.tools.files_read import ReadFileTool
from forge.tools.files_write import EditFileTool, WriteFileTool
from forge.tools.search import GlobTool, GrepTool, ListDirTool


def default_tools(skill_dirs: list[Path]) -> dict[str, Tool]:
    tools: list[Tool] = [
        BashTool(), ReadFileTool(), WriteFileTool(), EditFileTool(),
        GlobTool(), GrepTool(), ListDirTool(),
    ]
    # LoadSkillTool(skill_dirs) is appended here in the skills task
    return {t.name: t for t in tools}
