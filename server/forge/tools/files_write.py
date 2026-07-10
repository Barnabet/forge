from __future__ import annotations

from forge.tools.base import Tool, ToolContext, ToolResult


class WriteFileTool(Tool):
    name = "write_file"
    description = "Create or overwrite a file with the given content."
    params = {"type": "object", "properties": {
        "path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"]}

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        path = ctx.resolve(args["path"])
        before = path.read_text() if path.is_file() else None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(args["content"])
        cs = ctx.changesets.record(path, before, args["content"])
        stats = _stats(cs)
        return ToolResult(output=f"Wrote {args['path']} (+{cs.added}/−{cs.removed})",
                          diff_stats=stats)


class EditFileTool(Tool):
    name = "edit_file"
    description = ("Replace old_string with new_string in a file. old_string must "
                   "match exactly once unless replace_all is true.")
    params = {"type": "object", "properties": {
        "path": {"type": "string"}, "old_string": {"type": "string"},
        "new_string": {"type": "string"}, "replace_all": {"type": "boolean"}},
        "required": ["path", "old_string", "new_string"]}

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        path = ctx.resolve(args["path"])
        if not path.is_file():
            return ToolResult(output=f"File not found: {args['path']}", is_error=True)
        before = path.read_text()
        count = before.count(args["old_string"])
        if count == 0:
            return ToolResult(output="old_string not found in file", is_error=True)
        if count > 1 and not args.get("replace_all"):
            return ToolResult(
                output=f"old_string occurs {count} times; pass replace_all or add context",
                is_error=True)
        after = before.replace(args["old_string"], args["new_string"])
        path.write_text(after)
        cs = ctx.changesets.record(path, before, after)
        return ToolResult(output=f"Edited {args['path']} (+{cs.added}/−{cs.removed})",
                          diff_stats=_stats(cs))


def _stats(cs):
    from forge.engine.events import DiffStats
    return DiffStats(path=cs.path, added=cs.added, removed=cs.removed,
                     changeset_index=cs.index)
