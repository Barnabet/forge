from __future__ import annotations

from pathlib import Path

from forge.tools.base import Tool
from forge.tools.bash import BashTool
from forge.tools.files_read import ReadFileTool
from forge.tools.files_write import EditFileTool, WriteFileTool
from forge.tools.search import GlobTool, GrepTool, ListDirTool
from forge.tools.skills_tool import LoadSkillTool
from forge.tools.subagents import SpawnAgentsTool
from forge.tools.web import FetchPageTool, WebSearchTool


def web_tools_from_config(serper_api_key: str = "",
                          firecrawl_api_key: str = "") -> list[Tool]:
    tools: list[Tool] = []
    if serper_api_key:
        tools.append(WebSearchTool(serper_api_key))
    if firecrawl_api_key:
        tools.append(FetchPageTool(firecrawl_api_key))
    return tools


def default_tools(skill_dirs: list[Path], subagents: SpawnAgentsTool | None = None,
                  web_tools: list[Tool] | None = None) -> dict[str, Tool]:
    tools: list[Tool] = [
        BashTool(), ReadFileTool(), WriteFileTool(), EditFileTool(),
        GlobTool(), GrepTool(), ListDirTool(),
        LoadSkillTool(skill_dirs),
        *(web_tools or []),
    ]
    if subagents is not None:
        tools.append(subagents)
    return {t.name: t for t in tools}
