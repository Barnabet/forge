"""Agent control surface for session PTY terminals.

A single ``terminal`` tool with an ``action`` union (list/read/write/resize/
signal/close) lets the model interact with terminals opened via
``bash(display_terminal=true)``. It never opens terminals itself — that stays
gated behind bash approval — and never blocks waiting for prompts: reads and
writes settle for a small bounded interval at most.

Every operation goes through the ToolContext's ``TerminalController`` so the
tool holds no reference to SessionActor. Runtime misuse surfaces as
``TerminalControlError`` and is converted to a recoverable ToolResult error.
"""
from __future__ import annotations

import asyncio
import signal as signalmod

from forge.tools.base import (
    TerminalControlError,
    TerminalInfo,
    Tool,
    ToolContext,
    ToolResult,
)

# Bounded settle so a write/read can surface freshly produced output without
# ever hanging on an interactive prompt that never ends.
_MAX_SETTLE_MS = 2000
_DEFAULT_WRITE_SETTLE_MS = 200

# Max output bytes returned in a single read page. Larger windows are paginated
# forward via the advertised ``next_after`` cursor so nothing is ever silently
# dropped from the middle (unlike a middle-truncation, a forward page is fully
# recoverable by reading again).
_MAX_READ_BYTES = 30_000

# Only the signals the model legitimately needs: interrupt (Ctrl-C), polite
# terminate, and hard kill.
_SIGNALS = {
    "INT": signalmod.SIGINT,
    "TERM": signalmod.SIGTERM,
    "KILL": signalmod.SIGKILL,
}


class TerminalTool(Tool):
    name = "terminal"
    description = (
        "Interact with persistent PTY terminals opened via "
        "bash(display_terminal=true). One tool, selected by `action`:\n"
        "- list: all terminals (id, command, state, cwd, size, output cursor, "
        "exit info).\n"
        "- read: bounded output at/after `after` (a cursor from a prior read or "
        "from list's output_offset). Returns up to ~30k bytes from the start of "
        "the window plus the byte range returned and the available end cursor. "
        "If truncated, it advertises the next cursor: call read again with "
        "after=<that cursor> to page forward through the rest with no loss. A gap "
        "marker appears if old output scrolled out of the buffer.\n"
        "- write: send `data` to the terminal's input. Set enter:true to append "
        "a newline (submit the line/command). Returns promptly with any output "
        "produced during a short settle, plus the new cursor — read again if you "
        "need more. It never blocks waiting for a prompt.\n"
        "- resize: set cols/rows.\n"
        "- signal: send INT (Ctrl-C), TERM, or KILL to the process group.\n"
        "- close: terminate the terminal and reap its process group.\n"
        "Retain terminal ids and output cursors between calls.")
    params = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "read", "write", "resize", "signal", "close"],
            },
            "terminal_id": {
                "type": "string",
                "description": "Target terminal id (required for all actions "
                               "except list).",
            },
            "after": {
                "type": "integer",
                "description": "read: resume output from this byte cursor "
                               "(default 0 = from the start of the buffer).",
            },
            "data": {
                "type": "string",
                "description": "write: text to send to the terminal input.",
            },
            "enter": {
                "type": "boolean",
                "description": "write: append a carriage return to submit the "
                               "line. Default false.",
            },
            "cols": {"type": "integer", "description": "resize: columns."},
            "rows": {"type": "integer", "description": "resize: rows."},
            "signal": {
                "type": "string",
                "enum": ["INT", "TERM", "KILL"],
                "description": "signal: which signal to deliver.",
            },
            "settle_ms": {
                "type": "integer",
                "description": "read/write: wait up to this many ms (bounded, "
                               f"max {_MAX_SETTLE_MS}) for new output to appear "
                               "before returning.",
            },
        },
        "required": ["action"],
    }
    # Mutating (so it's excluded from plan mode), but interacting with a terminal
    # that bash already approved+opened in this session shouldn't re-prompt.
    read_only = False

    def display(self, args: dict) -> str:
        action = args.get("action", "terminal")
        tid = args.get("terminal_id")
        return f"terminal {action} {tid}" if tid else f"terminal {action}"

    def requires_approval(self, args: dict) -> bool:
        # The launch was already gated as bash; follow-up control of an
        # already-open terminal (id-scoped at the actor) doesn't spam approvals.
        return False

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        if ctx.terminals is None:
            return ToolResult(
                output="Terminals are not available in this context.",
                is_error=True)
        action = args.get("action")
        handler = {
            "list": self._list,
            "read": self._read,
            "write": self._write,
            "resize": self._resize,
            "signal": self._signal,
            "close": self._close,
        }.get(action)
        if handler is None:
            return ToolResult(
                output=f"Unknown terminal action: {action!r}", is_error=True)
        try:
            return await handler(args, ctx)
        except TerminalControlError as e:
            return ToolResult(output=str(e), is_error=True)

    # -- actions ------------------------------------------------------------
    async def _list(self, args: dict, ctx: ToolContext) -> ToolResult:
        infos = ctx.terminals.list()
        if not infos:
            return ToolResult(output="No terminals open.")
        return ToolResult(output="\n".join(_format_info(i) for i in infos))

    async def _read(self, args: dict, ctx: ToolContext) -> ToolResult:
        tid = _require_id(args)
        after = _clamp_cursor(args.get("after"))
        settle = _settle_ms(args.get("settle_ms"), default=0)
        if settle:
            await self._await_output(ctx, tid, after, settle)
        return _read_result(ctx, tid, after)

    async def _write(self, args: dict, ctx: ToolContext) -> ToolResult:
        tid = _require_id(args)
        data = args.get("data")
        if not isinstance(data, str):
            raise TerminalControlError("write requires string `data`.")
        if args.get("enter"):
            data += "\r"
        # Cursor before the write so we can report only the new output.
        _, _, before, _ = ctx.terminals.read(tid, 0)
        ctx.terminals.write(tid, data)
        settle = _settle_ms(args.get("settle_ms"), default=_DEFAULT_WRITE_SETTLE_MS)
        await self._await_output(ctx, tid, before, settle)
        result = _read_result(ctx, tid, before)
        if not result.is_error and not result.output.strip():
            return ToolResult(
                output=f"Wrote {len(data)} bytes; no output yet (cursor "
                       f"{before}). Use terminal read with after={before} to "
                       f"check for output.")
        return result

    async def _resize(self, args: dict, ctx: ToolContext) -> ToolResult:
        tid = _require_id(args)
        cols, rows = args.get("cols"), args.get("rows")
        if not (isinstance(cols, int) and isinstance(rows, int)
                and 1 <= cols <= 10_000 and 1 <= rows <= 10_000):
            raise TerminalControlError("resize requires cols/rows in 1..10000.")
        ctx.terminals.resize(tid, cols, rows)
        return ToolResult(output=f"Resized terminal {tid} to {cols}x{rows}.")

    async def _signal(self, args: dict, ctx: ToolContext) -> ToolResult:
        tid = _require_id(args)
        name = args.get("signal")
        sig = _SIGNALS.get(name)
        if sig is None:
            raise TerminalControlError(
                f"signal must be one of INT|TERM|KILL, got {name!r}.")
        ctx.terminals.signal(tid, sig)
        return ToolResult(output=f"Sent SIG{name} to terminal {tid}.")

    async def _close(self, args: dict, ctx: ToolContext) -> ToolResult:
        tid = _require_id(args)
        ctx.terminals.close(tid)
        return ToolResult(output=f"Closed terminal {tid}.")

    # -- helpers ------------------------------------------------------------
    async def _await_output(self, ctx: ToolContext, tid: str, after: int,
                            settle_ms: int) -> None:
        """Poll (bounded) until new output past ``after`` appears or the settle
        window elapses. Never blocks longer than ``settle_ms``."""
        if settle_ms <= 0:
            return
        deadline = asyncio.get_running_loop().time() + settle_ms / 1000
        while asyncio.get_running_loop().time() < deadline:
            _, _, end, _ = ctx.terminals.read(tid, after)
            if end > after:
                return
            await asyncio.sleep(0.02)


def _require_id(args: dict) -> str:
    tid = args.get("terminal_id")
    if not isinstance(tid, str) or not tid:
        raise TerminalControlError("terminal_id is required for this action.")
    return tid


def _clamp_cursor(value) -> int:
    return max(0, value) if isinstance(value, int) else 0


def _settle_ms(value, *, default: int) -> int:
    if not isinstance(value, int):
        return default
    return max(0, min(value, _MAX_SETTLE_MS))


def _read_result(ctx: ToolContext, tid: str, after: int) -> ToolResult:
    text, start, end, dropped = ctx.terminals.read(tid, after)
    # ``start`` is the byte offset of the first returned character (``after``
    # clamped up past any dropped output). Return at most _MAX_READ_BYTES bytes
    # from the START of this window and advertise a forward cursor so the omitted
    # tail is fully recoverable by reading again with after=next_after.
    body, consumed_bytes = _head_bytes(text, _MAX_READ_BYTES)
    next_after = start + consumed_bytes
    truncated = next_after < end
    header = f"[terminal {tid} bytes {start}..{next_after} (available end {end})]"
    if dropped:
        header += (f" (older output dropped: {after} < {start}; "
                   "some output was lost)")
    if truncated:
        header += (f" (truncated: {end - next_after} more bytes; call read again "
                   f"with after={next_after} to continue)")
    return ToolResult(output=f"{header}\n{body}" if body else header)


def _head_bytes(text: str, max_bytes: int) -> tuple[str, int]:
    """Return ``(prefix, byte_len)`` for the longest UTF-8 prefix of ``text``
    that fits in ``max_bytes`` without splitting a character."""
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text, len(raw)
    cut = max_bytes
    # Back off any UTF-8 continuation bytes so we cut on a character boundary.
    while cut > 0 and (raw[cut] & 0xC0) == 0x80:
        cut -= 1
    return raw[:cut].decode("utf-8"), cut


def _format_info(i: TerminalInfo) -> str:
    cmd = " ".join(i.command)
    parts = [f"{i.id}: {i.state} [{cmd}] cwd={i.cwd} {i.cols}x{i.rows} "
             f"cursor={i.output_offset}"]
    if i.exit_code is not None:
        parts.append(f"exit={i.exit_code}")
    if i.exit_reason:
        parts.append(f"reason={i.exit_reason}")
    return " ".join(parts)
