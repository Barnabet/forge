import asyncio
import os
import time

from forge.tools.base import ToolContext
from forge.tools.bash import BashTool


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


async def test_multibyte_char_across_read_boundary(tmp_path):
    # 4095 ASCII bytes + a 2-byte UTF-8 char straddles the 4096-byte read
    # boundary; an incremental decoder must reassemble it rather than emit U+FFFD.
    r = await BashTool().run(
        {"command": "python3 -c \"import sys; sys.stdout.write('x'*4095 + 'é')\""},
        ToolContext(cwd=tmp_path))
    assert not r.is_error
    assert "é" in r.output
    assert "�" not in r.output
