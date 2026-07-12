"""Tests for the agent-facing `terminal` control tool and its actor wiring.

Drive the tool through a real ActorTerminalController so cursor/dropped
semantics, enter/newline, bounded settle, error mapping, no-approval, and plan
exclusion are all exercised against the runtime rather than a stub."""
import asyncio
import re
import time

from forge.engine.actor import (
    ActorTerminalController,
    SessionActor,
    SessionMeta,
)
from forge.engine.bus import EventBus
from forge.engine.scheduler import Scheduler
from forge.llm.fake import FakeLLM
from forge.store.config import ForgeConfig
from forge.tools.base import ToolContext
from forge.tools.registry import default_tools
from forge.tools.terminal import TerminalTool


def make_actor(tmp_path):
    meta = SessionMeta(id="s1", cwd=str(tmp_path / "ws"), model="m")
    (tmp_path / "ws").mkdir(exist_ok=True)
    return SessionActor(
        meta=meta, home=tmp_path / "home", config=ForgeConfig(),
        llm=FakeLLM([]), bus=EventBus(), scheduler=Scheduler(3),
        system_prompt_fn=lambda m: "SYS")


def ctx_for(actor, tmp_path):
    return ToolContext(cwd=tmp_path / "ws",
                       terminals=ActorTerminalController(actor))


async def _wait_for(pred, timeout=5.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        await asyncio.sleep(interval)
    return False


def _body(output: str) -> str:
    """Return the payload of a read result (everything after the header line)."""
    return output.split("\n", 1)[1] if "\n" in output else ""


def _next_after(output: str) -> int:
    """Parse the advertised continuation cursor from a truncated read header."""
    m = re.search(r"call read again with after=(\d+)", output)
    assert m, f"no continuation cursor in header: {output.splitlines()[0]!r}"
    return int(m.group(1))


# -- registry / approval / plan mode ---------------------------------------
def test_registered_and_mutating(tmp_path):
    tools = default_tools([])
    assert "terminal" in tools
    assert tools["terminal"].read_only is False


def test_no_approval_required():
    t = TerminalTool()
    assert t.requires_approval({"action": "read", "terminal_id": "x"}) is False


def test_plan_mode_excludes_terminal(tmp_path):
    actor = make_actor(tmp_path)
    try:
        actor.set_mode("plan")
        names = {t.name for t in actor._active_tools()}
        assert "terminal" not in names
        assert "bash" not in names  # sanity: mutating tools excluded together
    finally:
        actor.teardown()


# -- actions ---------------------------------------------------------------
async def test_list_reports_terminal(tmp_path):
    actor = make_actor(tmp_path)
    try:
        term = await actor.open_terminal(["cat"], cols=90, rows=30)
        r = await TerminalTool().run({"action": "list"}, ctx_for(actor, tmp_path))
        assert not r.is_error
        assert term.id in r.output and "cat" in r.output
        assert "90x30" in r.output and "running" in r.output
    finally:
        actor.teardown()


async def test_list_empty(tmp_path):
    actor = make_actor(tmp_path)
    try:
        r = await TerminalTool().run({"action": "list"}, ctx_for(actor, tmp_path))
        assert not r.is_error and "No terminals" in r.output
    finally:
        actor.teardown()


async def test_write_enter_and_read_cursor(tmp_path):
    actor = make_actor(tmp_path)
    tool, ctx = TerminalTool(), ctx_for(actor, tmp_path)
    try:
        term = await actor.open_terminal(["cat"])
        r = await tool.run(
            {"action": "write", "terminal_id": term.id, "data": "hello",
             "enter": True, "settle_ms": 1000}, ctx)
        assert not r.is_error and "hello" in r.output
        # Read from end cursor: nothing new.
        _, _, end, _ = actor.read_terminal(term.id, 0)
        r2 = await tool.run(
            {"action": "read", "terminal_id": term.id, "after": end}, ctx)
        assert not r2.is_error
        assert f"{end}..{end}" in r2.output
    finally:
        actor.teardown()


async def test_write_appends_carriage_return_only_when_enter(tmp_path):
    actor = make_actor(tmp_path)
    tool, ctx = TerminalTool(), ctx_for(actor, tmp_path)
    try:
        term = await actor.open_terminal(["cat"])
        # No enter: cat echoes but no newline submitted; still returns promptly.
        r = await tool.run(
            {"action": "write", "terminal_id": term.id, "data": "abc",
             "settle_ms": 500}, ctx)
        assert not r.is_error
    finally:
        actor.teardown()


async def test_read_dropped_marker(tmp_path):
    actor = make_actor(tmp_path)
    tool, ctx = TerminalTool(), ctx_for(actor, tmp_path)
    try:
        term = await actor.open_terminal(["cat"])
        term.buffer.max_bytes = 4
        actor.write_terminal(term.id, "0123456789\n")
        assert await _wait_for(lambda: term.buffer.start > 0)
        r = await tool.run(
            {"action": "read", "terminal_id": term.id, "after": 0}, ctx)
        assert not r.is_error and "dropped" in r.output.lower()
    finally:
        actor.teardown()


async def test_read_paginates_large_multibyte_output_without_loss(tmp_path):
    actor = make_actor(tmp_path)
    tool, ctx = TerminalTool(), ctx_for(actor, tmp_path)
    try:
        # Produce > 30k bytes of multibyte content: 'é' is 2 bytes each, so
        # 20000 chars = 40000 bytes spans exactly two 30k pages.
        n = 20_000
        term = await actor.open_terminal(
            ["python3", "-c",
             f"import sys; sys.stdout.write('é'*{n}); sys.stdout.flush()"])
        # Wait until the full payload has arrived in the buffer.
        assert await _wait_for(
            lambda: actor.read_terminal(term.id, 0)[2] >= n * 2, timeout=8)

        # Page 1: from the start.
        r1 = await tool.run(
            {"action": "read", "terminal_id": term.id, "after": 0}, ctx)
        assert not r1.is_error
        assert "truncated" in r1.output
        next_after = _next_after(r1.output)
        # The advertised cursor must land on a UTF-8 boundary (even byte offset
        # here, since every char is 2 bytes).
        assert next_after % 2 == 0
        page1 = _body(r1.output)

        # Page 2: resume from the advertised cursor.
        r2 = await tool.run(
            {"action": "read", "terminal_id": term.id, "after": next_after}, ctx)
        assert not r2.is_error
        page2 = _body(r2.output)

        # Reconstructs all output with no loss and no duplication.
        assert page1 + page2 == "é" * n
        assert "\ufffd" not in page1 and "\ufffd" not in page2
        # Byte-correctness: page 1 consumed exactly next_after bytes.
        assert len(page1.encode("utf-8")) == next_after
    finally:
        actor.teardown()


async def test_read_page_boundary_does_not_split_utf8(tmp_path):
    actor = make_actor(tmp_path)
    tool, ctx = TerminalTool(), ctx_for(actor, tmp_path)
    try:
        # 3-byte chars won't divide 30000 evenly, forcing a mid-char backoff.
        n = 20_000  # 60k bytes of '€' (3 bytes each)
        term = await actor.open_terminal(
            ["python3", "-c",
             f"import sys; sys.stdout.write('€'*{n}); sys.stdout.flush()"])
        assert await _wait_for(
            lambda: actor.read_terminal(term.id, 0)[2] >= n * 3, timeout=8)

        r1 = await tool.run(
            {"action": "read", "terminal_id": term.id, "after": 0}, ctx)
        next_after = _next_after(r1.output)
        assert next_after % 3 == 0  # cut landed on a char boundary
        page1 = _body(r1.output)
        assert "\ufffd" not in page1

        # Drain remaining pages and confirm perfect reconstruction.
        acc = page1
        cursor = next_after
        while "truncated" in (r := await tool.run(
                {"action": "read", "terminal_id": term.id,
                 "after": cursor}, ctx)).output:
            acc += _body(r.output)
            cursor = _next_after(r.output)
        acc += _body(r.output)
        assert acc == "€" * n
    finally:
        actor.teardown()


async def test_write_then_read_paginates_safely(tmp_path):
    actor = make_actor(tmp_path)
    tool, ctx = TerminalTool(), ctx_for(actor, tmp_path)
    try:
        term = await actor.open_terminal(["cat"])
        big = "é" * 30_000  # 60k bytes echoed back by cat
        r = await tool.run(
            {"action": "write", "terminal_id": term.id, "data": big,
             "settle_ms": 1500}, ctx)
        assert not r.is_error
        # The write result is a bounded first page, not silently middle-truncated.
        assert len(_body(r.output).encode("utf-8")) <= 30_000
    finally:
        actor.teardown()


async def test_signal_terminates(tmp_path):
    actor = make_actor(tmp_path)
    tool, ctx = TerminalTool(), ctx_for(actor, tmp_path)
    try:
        term = await actor.open_terminal(["sleep", "60"])
        r = await tool.run(
            {"action": "signal", "terminal_id": term.id, "signal": "TERM"}, ctx)
        assert not r.is_error and "SIGTERM" in r.output
        assert await _wait_for(lambda: term.state == "exited")
    finally:
        actor.teardown()


async def test_close(tmp_path):
    actor = make_actor(tmp_path)
    tool, ctx = TerminalTool(), ctx_for(actor, tmp_path)
    try:
        term = await actor.open_terminal(["cat"])
        r = await tool.run(
            {"action": "close", "terminal_id": term.id}, ctx)
        assert not r.is_error and "Closed" in r.output
        assert term.state == "closed"
    finally:
        actor.teardown()


async def test_resize(tmp_path):
    actor = make_actor(tmp_path)
    tool, ctx = TerminalTool(), ctx_for(actor, tmp_path)
    try:
        term = await actor.open_terminal(["cat"])
        r = await tool.run(
            {"action": "resize", "terminal_id": term.id, "cols": 120,
             "rows": 40}, ctx)
        assert not r.is_error and "120x40" in r.output
        assert (term.cols, term.rows) == (120, 40)
    finally:
        actor.teardown()


# -- errors are recoverable ------------------------------------------------
async def test_unknown_terminal_is_recoverable(tmp_path):
    actor = make_actor(tmp_path)
    tool, ctx = TerminalTool(), ctx_for(actor, tmp_path)
    try:
        r = await tool.run({"action": "read", "terminal_id": "nope"}, ctx)
        assert r.is_error and "unknown terminal" in r.output.lower()
    finally:
        actor.teardown()


async def test_write_to_exited_is_recoverable(tmp_path):
    actor = make_actor(tmp_path)
    tool, ctx = TerminalTool(), ctx_for(actor, tmp_path)
    try:
        term = await actor.open_terminal(["python3", "-c", "print('x')"])
        assert await _wait_for(lambda: term.state == "exited")
        r = await tool.run(
            {"action": "write", "terminal_id": term.id, "data": "y",
             "settle_ms": 0}, ctx)
        assert r.is_error and "not running" in r.output.lower()
    finally:
        actor.teardown()


async def test_bad_signal_is_recoverable(tmp_path):
    actor = make_actor(tmp_path)
    tool, ctx = TerminalTool(), ctx_for(actor, tmp_path)
    try:
        term = await actor.open_terminal(["sleep", "60"])
        r = await tool.run(
            {"action": "signal", "terminal_id": term.id, "signal": "HUP"}, ctx)
        assert r.is_error and "INT|TERM|KILL" in r.output
    finally:
        actor.teardown()


async def test_missing_terminal_id_is_recoverable(tmp_path):
    actor = make_actor(tmp_path)
    tool, ctx = TerminalTool(), ctx_for(actor, tmp_path)
    try:
        r = await tool.run({"action": "read"}, ctx)
        assert r.is_error and "terminal_id is required" in r.output
    finally:
        actor.teardown()


async def test_unknown_action_is_recoverable(tmp_path):
    actor = make_actor(tmp_path)
    tool, ctx = TerminalTool(), ctx_for(actor, tmp_path)
    try:
        r = await tool.run({"action": "frobnicate"}, ctx)
        assert r.is_error and "Unknown terminal action" in r.output
    finally:
        actor.teardown()


async def test_no_controller_is_error(tmp_path):
    r = await TerminalTool().run({"action": "list"}, ToolContext(cwd=tmp_path))
    assert r.is_error and "not available" in r.output


def test_settle_ms_is_bounded():
    from forge.tools.terminal import _MAX_SETTLE_MS, _settle_ms
    assert _settle_ms(10_000, default=0) == _MAX_SETTLE_MS
    assert _settle_ms(-5, default=100) == 0
    assert _settle_ms(None, default=100) == 100
