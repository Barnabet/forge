from __future__ import annotations

import asyncio
import os
import signal

from forge.tools.base import Tool, ToolContext, ToolResult, truncate_middle


class BashTool(Tool):
    name = "bash"
    description = ("Run a shell command in the session working directory. "
                   "stdout and stderr are merged.")
    params = {"type": "object", "properties": {"command": {"type": "string"}},
              "required": ["command"]}

    def __init__(self, timeout_s: float = 120):
        self.timeout_s = timeout_s

    def display(self, args: dict) -> str:
        return args.get("command", "bash")

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        proc = await asyncio.create_subprocess_shell(
            args["command"], cwd=str(ctx.cwd),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            start_new_session=True)
        parts: list[str] = []
        try:
            async with asyncio.timeout(self.timeout_s):
                while True:
                    chunk = await proc.stdout.read(4096)
                    if not chunk:
                        break
                    text = chunk.decode(errors="replace")
                    parts.append(text)
                    ctx.emit_chunk(text)
                await proc.wait()
        except TimeoutError:
            _kill(proc)
            out = truncate_middle("".join(parts))
            return ToolResult(
                output=f"{out}\nCommand timed out after {self.timeout_s}s", is_error=True)
        except asyncio.CancelledError:
            _kill(proc)
            raise
        out = truncate_middle("".join(parts)).rstrip("\n")
        if proc.returncode != 0:
            return ToolResult(output=f"{out}\n(exit {proc.returncode})", is_error=True)
        return ToolResult(output=out)


def _kill(proc) -> None:
    if proc.returncode is None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
