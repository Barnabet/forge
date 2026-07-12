"""Actor-level terminal contract tests: durable TerminalState snapshots,
ephemeral TerminalOutput offsets, read start/end/dropped semantics, restart
reconciliation, and the non-running guards on write/resize/signal.

These drive the actor directly (no HTTP) with a fake LLM, mirroring
test_actor.py's harness. Timing waits use a bounded poll helper so they never
flake."""
import asyncio
import signal
import time

from forge.engine.actor import (
    SessionActor,
    SessionMeta,
    TerminalNotFound,
    TerminalNotRunning,
)
from forge.engine.bus import EventBus
from forge.engine.scheduler import Scheduler
from forge.llm.fake import FakeLLM
from forge.store.config import ForgeConfig

import pytest


def make_actor(tmp_path):
    meta = SessionMeta(id="s1", cwd=str(tmp_path / "ws"), model="m")
    (tmp_path / "ws").mkdir(exist_ok=True)
    actor = SessionActor(
        meta=meta, home=tmp_path / "home", config=ForgeConfig(),
        llm=FakeLLM([]), bus=EventBus(), scheduler=Scheduler(3),
        system_prompt_fn=lambda m: "SYS")
    return actor


async def _wait_for(pred, timeout=5.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        await asyncio.sleep(interval)
    return False


def _states(actor, tid=None):
    out = []
    for e in actor.log.read():
        if e.type == "terminal_state" and (tid is None or e.terminal_id == tid):
            out.append(e)
    return out


# -- durable TerminalState snapshots ---------------------------------------
async def test_open_emits_durable_running_snapshot(tmp_path):
    actor = make_actor(tmp_path)
    try:
        term = await actor.open_terminal(["cat"], cols=100, rows=30)
        snaps = _states(actor, term.id)
        assert len(snaps) == 1
        snap = snaps[0]
        assert snap.state in ("starting", "running")
        assert snap.command == ["cat"]
        assert snap.cols == 100 and snap.rows == 30
        assert snap.cwd == str(tmp_path / "ws")
    finally:
        actor.teardown()


async def test_resize_emits_snapshot_and_noop_is_skipped(tmp_path):
    actor = make_actor(tmp_path)
    try:
        term = await actor.open_terminal(["cat"], cols=80, rows=24)
        before = len(_states(actor, term.id))
        actor.resize_terminal(term.id, 120, 40)
        assert len(_states(actor, term.id)) == before + 1
        assert (term.cols, term.rows) == (120, 40)
        # A no-op resize to the same dimensions emits no redundant snapshot.
        actor.resize_terminal(term.id, 120, 40)
        assert len(_states(actor, term.id)) == before + 1
    finally:
        actor.teardown()


async def test_exit_emits_final_snapshot(tmp_path):
    actor = make_actor(tmp_path)
    try:
        term = await actor.open_terminal(["python3", "-c", "print('bye')"])
        assert await _wait_for(lambda: term.state == "exited")
        # Reader/exit hooks run on the loop; give the exit snapshot a beat.
        assert await _wait_for(
            lambda: any(s.state == "exited" for s in _states(actor, term.id)))
        final = _states(actor, term.id)[-1]
        assert final.state == "exited"
        assert final.exit_code == 0
        assert final.exit_reason == "exited"
    finally:
        actor.teardown()


async def test_close_emits_snapshot_once(tmp_path):
    actor = make_actor(tmp_path)
    try:
        term = await actor.open_terminal(["cat"])
        actor.close_terminal(term.id)
        n = len(_states(actor, term.id))
        assert _states(actor, term.id)[-1].state == "closed"
        # Repeated close is a no-op and emits no further snapshot.
        actor.close_terminal(term.id)
        assert len(_states(actor, term.id)) == n
    finally:
        actor.teardown()


# -- ephemeral TerminalOutput offsets --------------------------------------
async def test_output_events_are_ephemeral_with_offsets(tmp_path):
    actor = make_actor(tmp_path)
    q = actor.bus.subscribe()
    try:
        term = await actor.open_terminal(["cat"])
        actor.write_terminal(term.id, "hello\n")

        chunks = []
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and "hello" not in "".join(
                c.text for c in chunks):
            try:
                ev = await asyncio.wait_for(q.get(), timeout=0.2)
            except asyncio.TimeoutError:
                continue
            if getattr(ev, "type", None) == "terminal_output":
                chunks.append(ev)
        assert chunks, "no terminal_output published"
        for ev in chunks:
            assert ev.end_offset - ev.start_offset == len(ev.text.encode())
        # Offsets are monotonic and match the ring buffer's end cursor.
        assert chunks[-1].end_offset == term.buffer.end
        # TerminalOutput is never written to the durable log.
        assert all(e.type != "terminal_output" for e in actor.log.read())
    finally:
        actor.bus.unsubscribe(q)
        actor.teardown()


# -- read start/end/dropped semantics --------------------------------------
async def test_read_terminal_reports_start_end_dropped(tmp_path):
    actor = make_actor(tmp_path)
    try:
        term = await actor.open_terminal(["cat"])
        actor.write_terminal(term.id, "abc\n")
        assert await _wait_for(lambda: "abc" in actor.read_terminal(term.id, 0)[0])
        text, start, end, dropped = actor.read_terminal(term.id, 0)
        assert "abc" in text
        assert start == 0 and end == term.buffer.end and dropped is False

        # Shrink the ring so old output is evicted, then read from a dropped
        # offset: dropped is True and start clamps to the retained window.
        term.buffer.max_bytes = 4
        actor.write_terminal(term.id, "0123456789\n")
        assert await _wait_for(lambda: term.buffer.start > 0)
        _, start2, _, dropped2 = actor.read_terminal(term.id, 0)
        assert dropped2 is True
        assert start2 == term.buffer.start
    finally:
        actor.teardown()


# -- restart / rehydrate reconciliation ------------------------------------
async def test_reconcile_skips_terminal_with_final_snapshot(tmp_path):
    """A terminal whose log tail is closed/exited is not orphaned on restart."""
    actor = make_actor(tmp_path)
    term = await actor.open_terminal(["cat"], cols=90, rows=20)
    tid = term.id
    actor.close_terminal(term.id)  # writes a durable "closed" snapshot
    assert _states(actor, tid)[-1].state == "closed"
    actor.terminals._terminals.clear()  # emulate empty registry after restart
    actor.reconcile_terminals()
    orphaned = [s for s in _states(actor, tid) if s.state == "orphaned"]
    assert orphaned == []
    actor.teardown()


async def test_reconcile_orphans_a_running_tail(tmp_path):
    """A log whose last terminal snapshot is 'running' (no exit/close persisted)
    gets an orphaned reconciliation record on rehydrate."""
    actor = make_actor(tmp_path)
    term = await actor.open_terminal(["cat"], cols=77, rows=21)
    tid = term.id
    # Snapshot state is "running" in the log; tear down the OS process only.
    term._reaped = True
    term.state = "closed"  # runtime only; the durable log still says running
    actor.terminals._terminals.clear()  # emulate empty registry after restart
    actor.reconcile_terminals()
    orphaned = [s for s in _states(actor, tid) if s.state == "orphaned"]
    assert orphaned, "running tail should produce an orphaned snapshot"
    snap = orphaned[-1]
    assert snap.exit_reason == "orphaned"
    assert snap.exit_code is None
    assert (snap.cols, snap.rows) == (77, 21)
    actor.teardown()


# -- non-running guards -----------------------------------------------------
async def test_write_resize_signal_on_exited_raise_not_running(tmp_path):
    actor = make_actor(tmp_path)
    try:
        term = await actor.open_terminal(["python3", "-c", "print('x')"])
        assert await _wait_for(lambda: term.state == "exited")
        with pytest.raises(TerminalNotRunning):
            actor.write_terminal(term.id, "y")
        with pytest.raises(TerminalNotRunning):
            actor.resize_terminal(term.id, 100, 30)
        with pytest.raises(TerminalNotRunning):
            actor.signal_terminal(term.id, signal.SIGINT)
    finally:
        actor.teardown()


async def test_signal_running_terminal_delivers(tmp_path):
    actor = make_actor(tmp_path)
    try:
        term = await actor.open_terminal(["sleep", "60"])
        actor.signal_terminal(term.id, signal.SIGTERM)
        assert await _wait_for(lambda: term.state == "exited")
        assert term.exit_reason == "signaled"
    finally:
        actor.teardown()


async def test_unknown_terminal_id_raises_not_found(tmp_path):
    actor = make_actor(tmp_path)
    try:
        with pytest.raises(TerminalNotFound):
            actor.read_terminal("nope")
        with pytest.raises(TerminalNotFound):
            actor.write_terminal("nope", "x")
        with pytest.raises(TerminalNotFound):
            actor.signal_terminal("nope", signal.SIGINT)
    finally:
        actor.teardown()


# -- rewind closes live terminals before workspace restore ------------------
async def test_rewind_closes_live_terminals_and_emits_final_snapshot(tmp_path):
    """A history rewind tears down every live terminal (killing its process
    group before the destructive workspace restore) and persists a final
    ``closed`` lifecycle snapshot for each."""
    from forge.engine.events import MessageCheckpointed, UserMessage

    actor = make_actor(tmp_path)
    try:
        ev = actor.emit(actor._e(UserMessage, text="hi", images=[]))
        cp = actor.checkpoints.capture(label="cp").id
        actor.emit(actor._e(MessageCheckpointed, user_seq=ev.seq, checkpoint=cp))
        term = await actor.open_terminal(["sleep", "60"])
        tid = term.id
        assert term.is_live()
        await actor.rewind(ev.seq)
        assert not term.is_live()
        assert _states(actor, tid)[-1].state == "closed"
    finally:
        actor.teardown()
