from __future__ import annotations

from typing import TYPE_CHECKING

from forge.tools.base import Tool, ToolContext, ToolResult

if TYPE_CHECKING:
    from forge.engine.fileindex import FileIndex, FileSnippet


def format_file_snippet(s: "FileSnippet") -> str:
    return f"[{s.path}:{s.start_line}-{s.end_line} score={s.score:.2f}]\n{s.text}"


class SearchFilesTool(Tool):
    name = "search_files"
    description = (
        "Semantic search over the text content of files in the project folder "
        "(source, docs, PDFs). Finds relevant passages by meaning, not exact "
        "string match — use it to locate where a concept, feature, or behavior "
        "lives when you don't know the keyword; use grep for exact strings. "
        "Returns ranked snippets with file path and line range; open the file to "
        "read surrounding context.")
    params = {"type": "object", "properties": {
        "query": {"type": "string", "description": "what to look for, in plain language"},
        "top_k": {"type": "integer", "description": "max results (default 8)"},
    }, "required": ["query"]}
    read_only = True

    def __init__(self, index: "FileIndex", project_id: str | None):
        self.index = index
        self.project_id = project_id

    def display(self, args: dict) -> str:
        return args.get("query") or self.name

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        top_k = args.get("top_k") or 8
        snippets = await self.index.search(
            args["query"], ctx.cwd, self.project_id, top_k=top_k)
        if not snippets:
            return ToolResult(output="No files matched.")
        return ToolResult(
            output="\n\n".join(format_file_snippet(s) for s in snippets))
