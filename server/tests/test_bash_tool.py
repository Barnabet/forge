import asyncio
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
