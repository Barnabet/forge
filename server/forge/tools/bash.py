from __future__ import annotations

import asyncio
import codecs
import os
import signal

from forge.tools.base import (
    TerminalControlError,
    Tool,
    ToolContext,
    ToolResult,
    truncate_middle,
)


class BashTool(Tool):
    name = "bash"
    description = (
        "Run a shell command in the session working directory. stdout and "
        "stderr are merged. Use this for finite commands that run to completion: "
        "the tool streams output and returns when the process exits (or times "
        "out).\n"
        "Set display_terminal:true to launch the command in a persistent, "
        "user-visible PTY terminal instead: use it for long-lived servers, "
        "interactive programs (REPLs, TUIs), or output the user should watch. "
        "That path returns immediately with a terminal id (it does NOT wait for "
        "the process); the terminal outlives this run. Keep the returned id and "
        "use the `terminal` tool (read/write/signal/close) to interact with it.")
    params = {"type": "object", "properties": {
        "command": {"type": "string"},
        "display_terminal": {
            "type": "boolean",
            "description": ("Launch in a persistent PTY terminal and return "
                            "immediately with its id, instead of running to "
                            "completion. Default false."),
        },
    }, "required": ["command"]}

    def __init__(self, timeout_s: float = 120):
        self.timeout_s = timeout_s

    def display(self, args: dict) -> str:
        cmd = args.get("command", "bash")
        if args.get("display_terminal"):
            return f"{cmd}  (in terminal)"
        return cmd

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        if args.get("display_terminal"):
            return await self._launch_terminal(args, ctx)
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

    async def _launch_terminal(self, args: dict, ctx: ToolContext) -> ToolResult:
        if ctx.terminals is None:
            return ToolResult(
                output="Terminals are not available in this context; run the "
                       "command without display_terminal.", is_error=True)
        # Launch the same shell semantics as normal bash (/bin/sh -c command) but
        # on a PTY that outlives this run. Returns immediately without waiting.
        try:
            tid = await ctx.terminals.open(["/bin/sh", "-c", args["command"]],
                                           cwd=str(ctx.cwd))
        except TerminalControlError as e:
            return ToolResult(output=f"Could not open terminal: {e}", is_error=True)
        return ToolResult(
            output=f"Started in terminal {tid}. It runs independently of this "
                   f"run; use the terminal tool with terminal_id={tid!r} to read "
                   f"output, write input, signal, or close it.")


def _kill(proc) -> None:
    # start_new_session=True makes the process group id equal proc.pid, so we can
    # signal the whole group unconditionally -- even after the shell leader exited,
    # to reap any backgrounded children that inherited our stdout.
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
