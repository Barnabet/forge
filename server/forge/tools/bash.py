from __future__ import annotations

import asyncio
import codecs
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
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            async with asyncio.timeout(self.timeout_s):
                while True:
                    chunk = await proc.stdout.read(4096)
                    if not chunk:
                        break
                    text = decoder.decode(chunk)
                    if text:
                        parts.append(text)
                        ctx.emit_chunk(text)
                tail = decoder.decode(b"", final=True)
                if tail:
                    parts.append(tail)
                    ctx.emit_chunk(tail)
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
    # start_new_session=True makes the process group id equal proc.pid, so we can
    # signal the whole group unconditionally -- even after the shell leader exited,
    # to reap any backgrounded children that inherited our stdout.
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
