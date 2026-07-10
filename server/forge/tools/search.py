from __future__ import annotations

import asyncio
import re
import shutil

from forge.tools.base import Tool, ToolContext, ToolResult, truncate_middle

SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__", "dist", "build"}


class ListDirTool(Tool):
    name = "list_dir"
    description = "List directory entries; directories have a trailing /."
    params = {"type": "object", "properties": {"path": {"type": "string"}}}
    read_only = True

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        path = ctx.resolve(args.get("path", "."))
        if not path.is_dir():
            return ToolResult(output=f"Not a directory: {path}", is_error=True)
        entries = sorted(p.name + ("/" if p.is_dir() else "") for p in path.iterdir())
        return ToolResult(output="\n".join(entries) or "(empty)")


class GlobTool(Tool):
    name = "glob"
    description = "Find files by glob pattern relative to the working directory."
    params = {"type": "object", "properties": {"pattern": {"type": "string"}},
              "required": ["pattern"]}
    read_only = True

    def display(self, args: dict) -> str:
        return args.get("pattern", self.name)

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        hits = [str(p.relative_to(ctx.cwd)) for p in ctx.cwd.glob(args["pattern"])
                if not any(part in SKIP_DIRS for part in p.parts)]
        hits = sorted(hits)[:200]
        return ToolResult(output="\n".join(hits) or "No matches.")


class GrepTool(Tool):
    name = "grep"
    description = "Search file contents with a regex. Output: path:line:text."
    params = {"type": "object", "properties": {
        "pattern": {"type": "string"}, "path": {"type": "string"}},
        "required": ["pattern"]}
    read_only = True

    def display(self, args: dict) -> str:
        return args.get("pattern", self.name)

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        root = ctx.resolve(args.get("path", "."))
        if shutil.which("rg"):
            proc = await asyncio.create_subprocess_exec(
                "rg", "-n", "--no-heading", "--max-count", "100",
                args["pattern"], str(root),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            out, _ = await proc.communicate()
            text = out.decode(errors="replace").strip()
            return ToolResult(output=truncate_middle(text) or "No matches.")
        # Python fallback
        rx = re.compile(args["pattern"])
        lines: list[str] = []
        files = [root] if root.is_file() else [
            p for p in root.rglob("*")
            if p.is_file() and not any(part in SKIP_DIRS for part in p.parts)]
        for f in files:
            try:
                for i, line in enumerate(f.read_text().splitlines(), 1):
                    if rx.search(line):
                        lines.append(f"{f.relative_to(ctx.cwd)}:{i}:{line}")
                        if len(lines) >= 100:
                            raise StopIteration
            except (UnicodeDecodeError, StopIteration, ValueError):
                continue
        return ToolResult(output="\n".join(lines) or "No matches.")
