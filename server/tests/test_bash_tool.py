import asyncio
import os
import time

from forge.tools.base import TerminalControlError, TerminalInfo, ToolContext
from forge.tools.bash import BashTool


class FakeTerminals:
    """Minimal in-memory TerminalController for BashTool launch tests."""

    def __init__(self, fail: str | None = None):
        self.opened: list[tuple[list[str], str | None]] = []
        self.fail = fail
        self.procs: list = []

    async def open(self, argv, *, cwd=None, cols=80, rows=24):
        if self.fail:
            raise TerminalControlError(self.fail)
        self.opened.append((argv, cwd))
        proc = await asyncio.create_subprocess_exec(*argv)
        self.procs.append(proc)
        return f"t{len(self.opened)}"

    def list(self):
        return []

    def info(self, tid):
        return TerminalInfo(id=tid, command=[], state="running", cwd="/",
                            cols=80, rows=24, output_offset=0)

    def read(self, tid, after=0):
        return "", after, after, False

    def write(self, tid, data):
        pass

    def resize(self, tid, cols, rows):
        pass

    def signal(self, tid, sig):
        pass

    def close(self, tid):
        pass


async def test_runs_and_streams(tmp_path):
    chunks: list[str] = []
    ctx = ToolContext(cwd=tmp_path, emit_chunk=chunks.append)
    r = await BashTool().run({"command": "echo hi && echo err >&2"}, ctx)
    assert not r.is_error
    assert "hi" in r.output and "err" in r.output  # stderr merged
    # streamed chunks match persisted output, modulo the trailing-newline trim
    assert "".join(chunks).rstrip("\n") == r.output


async def test_nonzero_exit_is_error(tmp_path):
    r = await BashTool().run({"command": "exit 3"}, ToolContext(cwd=tmp_path))
    assert r.is_error and "(exit 3)" in r.output


async def test_timeout_kills_process_group(tmp_path):
    start = time.monotonic()
    r = await BashTool(timeout_s=0.3).run({"command": "sleep 30"}, ToolContext(cwd=tmp_path))
    assert time.monotonic() - start < 5
    assert r.is_error and "timed out" in r.output.lower()


async def test_cancellation_kills_process(tmp_path):
    task = asyncio.create_task(
        BashTool().run({"command": "sleep 30"}, ToolContext(cwd=tmp_path)))
    await asyncio.sleep(0.2)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_timeout_kills_backgrounded_child(tmp_path):
    # The shell leader exits (returncode 0) before the timeout, but the
    # backgrounded sleep inherits our stdout and keeps the read loop blocked
    # until we time out. The whole group must still be SIGKILLed.
    pid_file = tmp_path / "pid"
    r = await BashTool(timeout_s=0.5).run(
        {"command": f"sleep 30 & echo $! > {pid_file}; echo hi"},
        ToolContext(cwd=tmp_path))
    assert r.is_error and "timed out" in r.output.lower()
    pid = int(pid_file.read_text().strip())
    # Poll briefly to absorb SIGKILL delivery latency.
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(0.05)
    else:
        raise AssertionError(f"backgrounded child {pid} survived the timeout")


def test_schema_has_optional_display_terminal():
    props = BashTool().params["properties"]
    assert props["display_terminal"]["type"] == "boolean"
    assert "display_terminal" not in BashTool().params["required"]


def test_display_indicates_terminal():
    t = BashTool()
    assert t.display({"command": "npm run dev"}) == "npm run dev"
    assert "terminal" in t.display({"command": "npm run dev", "display_terminal": True})


async def test_normal_path_ignores_missing_terminals(tmp_path):
    # display_terminal defaults false: normal path works with no controller.
    r = await BashTool().run({"command": "echo hi"}, ToolContext(cwd=tmp_path))
    assert not r.is_error and "hi" in r.output


async def test_display_terminal_returns_id_immediately(tmp_path):
    terms = FakeTerminals()
    ctx = ToolContext(cwd=tmp_path, terminals=terms)
    start = time.monotonic()
    r = await BashTool().run(
        {"command": "sleep 30", "display_terminal": True}, ctx)
    elapsed = time.monotonic() - start
    assert elapsed < 5  # returns without waiting for the process
    assert not r.is_error
    assert "terminal t1" in r.output
    # Launched via /bin/sh -c at the session cwd.
    argv, cwd = terms.opened[0]
    assert argv == ["/bin/sh", "-c", "sleep 30"]
    assert cwd == str(tmp_path)
    for p in terms.procs:
        p.kill()


async def test_display_terminal_process_survives_tool_return(tmp_path):
    terms = FakeTerminals()
    ctx = ToolContext(cwd=tmp_path, terminals=terms)
    await BashTool().run({"command": "sleep 30", "display_terminal": True}, ctx)
    proc = terms.procs[0]
    assert proc.returncode is None  # still live after the tool returned
    proc.kill()
    await proc.wait()


async def test_display_terminal_without_controller_is_error(tmp_path):
    r = await BashTool().run(
        {"command": "sleep 1", "display_terminal": True}, ToolContext(cwd=tmp_path))
    assert r.is_error and "not available" in r.output


async def test_display_terminal_open_error_is_recoverable(tmp_path):
    terms = FakeTerminals(fail="session already has 4 live terminals")
    ctx = ToolContext(cwd=tmp_path, terminals=terms)
    r = await BashTool().run(
        {"command": "sleep 1", "display_terminal": True}, ctx)
    assert r.is_error and "4 live terminals" in r.output


async def test_multibyte_char_across_read_boundary(tmp_path):
    # 4095 ASCII bytes + a 2-byte UTF-8 char straddles the 4096-byte read
    # boundary; an incremental decoder must reassemble it rather than emit U+FFFD.
    r = await BashTool().run(
        {"command": "python3 -c \"import sys; sys.stdout.write('x'*4095 + 'é')\""},
        ToolContext(cwd=tmp_path))
    assert not r.is_error
    assert "é" in r.output
    assert "�" not in r.output
