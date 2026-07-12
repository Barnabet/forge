from __future__ import annotations

import hashlib

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
        # Read raw bytes once and register their exact hash as this session's
        # baseline, so a later stale-write guard compares against the content the
        # agent actually saw (not a possibly-changed re-read). Hashing the same
        # bytes we decode keeps the baseline consistent with ``current_hash``,
        # which also hashes on-disk bytes.
        data = path.read_bytes()
        if ctx.shared_workspace is not None:
            ctx.shared_workspace.observe_hash(
                ctx.baseline_owner, path, hashlib.sha256(data).hexdigest())
        text = data.decode(errors="replace")
        lines = text.splitlines()
        start = max(args.get("offset", 1), 1)
        limit = args.get("limit", 2000)
        window = lines[start - 1:start - 1 + limit]
        body = "\n".join(f"{i:>6}\t{line}" for i, line in enumerate(window, start))
        return ToolResult(output=truncate_middle(body))
