from __future__ import annotations

from pathlib import Path

from forge.tools.base import Tool
from forge.tools.bash import BashTool
from forge.tools.files_read import ReadFileTool
from forge.tools.files_write import EditFileTool, WriteFileTool
from forge.tools.images import CreateImageTool
from forge.tools.pdf import ReadPdfTool, ViewTool
from forge.tools.plan import ProposePlanTool
from forge.tools.search import GlobTool, GrepTool, ListDirTool
from forge.tools.skills_tool import LoadSkillTool
from forge.tools.subagents import SpawnAgentsTool
from forge.tools.terminal import TerminalTool
from forge.tools.todos import UpdateTodosTool
from forge.tools.web import FetchPageTool, WebSearchTool


def web_tools_from_config(serper_api_key: str = "",
                          firecrawl_api_key: str = "") -> list[Tool]:
    tools: list[Tool] = []
    if serper_api_key:
        tools.append(WebSearchTool(serper_api_key))
    if firecrawl_api_key:
        tools.append(FetchPageTool(firecrawl_api_key))
    return tools


def image_tool_from_config(openrouter_api_key: str = "",
                           image_model: str = "google/gemini-3.1-flash-lite-image") -> Tool | None:
    if not openrouter_api_key:
        return None
    return CreateImageTool(openrouter_api_key, image_model)


def default_tools(skill_dirs: list[Path], subagents: SpawnAgentsTool | None = None,
                  web_tools: list[Tool] | None = None,
                  memory_tools: list[Tool] | None = None,
                  image_tool: Tool | None = None,
                  file_search_tool: Tool | None = None) -> dict[str, Tool]:
    tools: list[Tool] = [
        BashTool(), TerminalTool(), ReadFileTool(), ReadPdfTool(), ViewTool(),
        WriteFileTool(), EditFileTool(),
        GlobTool(), GrepTool(), ListDirTool(),
        LoadSkillTool(skill_dirs), UpdateTodosTool(), ProposePlanTool(),
        *(web_tools or []),
        *(memory_tools or []),
    ]
    if file_search_tool is not None:
        tools.append(file_search_tool)
    if image_tool is not None:
        tools.append(image_tool)
    if subagents is not None:
        tools.append(subagents)
    return {t.name: t for t in tools}
