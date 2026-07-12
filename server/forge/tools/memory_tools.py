from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from forge.engine.memory import (
    GLOBAL_REGIONS, PROJECT_REGIONS, global_memory_dir, project_memory_dir,
)
from forge.tools.base import Tool, ToolContext, ToolResult, truncate_middle

if TYPE_CHECKING:
    from forge.engine.memindex import MemoryIndex, Snippet


def format_snippet(s: "Snippet") -> str:
    return (f"[{s.tier}/{s.region}:{s.start_line}-{s.end_line} "
            f"score={s.score:.2f}]\n{s.text}")


class RememberTool(Tool):
    name = "remember"
    description = (
        "Targeted semantic search over your long-term memory (global + project tiers). "
        "Relevant snippets are already recalled automatically below each user message, "
        "so use this only when a specific detail you need wasn't recalled — not as a "
        "reflexive first step. Returns matching snippets with tier/region and line "
        "numbers; follow up with read_memory to see surrounding context.")
    params = {"type": "object", "properties": {
        "query": {"type": "string", "description": "what to recall"},
    }, "required": ["query"]}
    read_only = True

    def __init__(self, index: "MemoryIndex", project_id: str | None):
        self.index = index
        self.project_id = project_id

    def display(self, args: dict) -> str:
        return args.get("query") or self.name

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        snippets = await self.index.search(args["query"], self.project_id)
        if not snippets:
            return ToolResult(output="No memories matched.")
        return ToolResult(output="\n\n".join(format_snippet(s) for s in snippets))


class ReadMemoryTool(Tool):
    name = "read_memory"
    description = (
        "Read a long-term memory region. Call without arguments to list regions "
        "and sizes. Returns 1-indexed, line-numbered content.")
    params = {"type": "object", "properties": {
        "tier": {"type": "string", "enum": ["global", "project"]},
        "region": {"type": "string"},
        "offset": {"type": "integer", "description": "1-indexed first line"},
        "limit": {"type": "integer", "description": "max lines (default 2000)"},
    }, "required": []}
    read_only = True

    def __init__(self, home: Path, project_id: str | None):
        self.home = home
        self.project_id = project_id

    def display(self, args: dict) -> str:
        tier, region = args.get("tier"), args.get("region")
        return f"{tier}/{region}" if tier and region else "list regions"

    def _tiers(self) -> list[tuple[str, Path, tuple[str, ...]]]:
        tiers: list[tuple[str, Path, tuple[str, ...]]] = [
            ("global", global_memory_dir(self.home), GLOBAL_REGIONS)]
        if self.project_id and (pdir := project_memory_dir(self.home, self.project_id)):
            tiers.append(("project", pdir, PROJECT_REGIONS))
        return tiers

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        tier, region = args.get("tier"), args.get("region")
        if not tier or not region:
            lines = []
            for name, dir_, regions in self._tiers():
                for r in regions:
                    p = dir_ / f"{r}.md"
                    n = len(p.read_text()) if p.is_file() else 0
                    lines.append(f"{name}/{r} — {n} chars")
            return ToolResult(output="\n".join(lines))
        match = [(d, rs) for name, d, rs in self._tiers() if name == tier]
        if not match:
            return ToolResult(output=f"Tier not available: {tier}", is_error=True)
        dir_, regions = match[0]
        if region not in regions:
            return ToolResult(
                output=f"Unknown {tier} region: {region} (valid: {', '.join(regions)})",
                is_error=True)
        path = dir_ / f"{region}.md"
        if not path.is_file():
            return ToolResult(output="(empty)")
        lines = path.read_text().splitlines()
        start = max(args.get("offset", 1), 1)
        limit = args.get("limit", 2000)
        window = lines[start - 1:start - 1 + limit]
        body = "\n".join(f"{i:>6}\t{line}" for i, line in enumerate(window, start))
        return ToolResult(output=truncate_middle(body) or "(empty)")
