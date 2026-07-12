from __future__ import annotations

import hashlib

from forge.tools.base import Tool, ToolContext, ToolResult


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _guard_stale(ctx: ToolContext, path):
    """Return a ToolResult error when the file changed since this session last
    observed it, else None. Runs under the workspace lock (mutating tools hold
    it), so the check is consistent with the impending write."""
    ws = ctx.shared_workspace
    if ws is None:
        return None
    msg = ws.detect_stale(ctx.baseline_owner, path)
    if msg is not None:
        return ToolResult(output=msg, is_error=True)
    return None


def _record(ctx: ToolContext, path, action: str, before: str | None,
            after: str, before_hash: str | None):
    """Record the changeset (with provenance) and the workspace activity, and
    refresh this session's baseline. Returns the Changeset."""
    after_hash = _hash(after)
    cs = ctx.changesets.record(
        path, before, after, session_id=ctx.session_id,
        call_id=ctx.call_id or None, before_hash=before_hash,
        after_hash=after_hash)
    ws = ctx.shared_workspace
    if ws is not None:
        key = str(ws.canonical(path))
        recorded_action = (f"{ctx.activity_action_prefix}: {action}"
                           if ctx.activity_action_prefix else action)
        ws.record_controlled_change(
            session_id=ctx.session_id, action=recorded_action, paths=[path],
            origin=ctx.activity_origin, call_id=ctx.call_id or None,
            before={key: before_hash}, baseline_owner=ctx.baseline_owner)
    return cs


class WriteFileTool(Tool):
    name = "write_file"
    description = "Create or overwrite a file with the given content."
    params = {"type": "object", "properties": {
        "path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"]}

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        path = ctx.resolve(args["path"])
        stale = _guard_stale(ctx, path)
        if stale is not None:
            return stale
        before = path.read_text() if path.is_file() else None
        before_hash = _hash(before) if before is not None else None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(args["content"])
        cs = _record(ctx, path, "write_file", before, args["content"], before_hash)
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
        stale = _guard_stale(ctx, path)
        if stale is not None:
            return stale
        before = path.read_text()
        before_hash = _hash(before)
        count = before.count(args["old_string"])
        if count == 0:
            return ToolResult(output="old_string not found in file", is_error=True)
        if count > 1 and not args.get("replace_all"):
            return ToolResult(
                output=f"old_string occurs {count} times; pass replace_all or add context",
                is_error=True)
        after = before.replace(args["old_string"], args["new_string"])
        path.write_text(after)
        cs = _record(ctx, path, "edit_file", before, after, before_hash)
        return ToolResult(output=f"Edited {args['path']} (+{cs.added}/−{cs.removed})",
                          diff_stats=_stats(cs))


def _stats(cs):
    from forge.engine.events import DiffStats
    return DiffStats(path=cs.path, added=cs.added, removed=cs.removed,
                     changeset_index=cs.index, diff=cs.diff)
