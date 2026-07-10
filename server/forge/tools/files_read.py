from __future__ import annotations

from forge.tools.base import Tool, ToolContext, ToolResult, truncate_middle


class ReadFileTool(Tool):
    name = "read_file"
    description = "Read a file. Returns 1-indexed, line-numbered content."
    params = {"type": "object", "properties": {
        "path": {"type": "string"},
        "offset": {"type": "integer", "description": "1-indexed first line"},
        "limit": {"type": "integer", "description": "max lines (default 2000)"},
    }, "required": ["path"]}
    read_only = True

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        path = ctx.resolve(args["path"])
        if not path.is_file():
            return ToolResult(output=f"File not found: {args['path']}", is_error=True)
        lines = path.read_text(errors="replace").splitlines()
        start = max(args.get("offset", 1), 1)
        limit = args.get("limit", 2000)
        window = lines[start - 1:start - 1 + limit]
        body = "\n".join(f"{i:>6}\t{line}" for i, line in enumerate(window, start))
        return ToolResult(output=truncate_middle(body))
