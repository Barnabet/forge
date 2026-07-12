import asyncio
import time

import pytest
from starlette.testclient import TestClient

from forge.api.app import create_app
from forge.engine.actor import (
    RewindNoCheckpoint, RewindTargetInactive, RewindTargetMissing,
    RewindWorkspaceError,
)
from forge.engine.bus import EventBus
from forge.engine.events import (
    AssistantMessage, AutonomyChanged, HistoryRewound, RunFinished, UserMessage,
)
from forge.engine.manager import SessionManager
from forge.engine.projection import active_events, to_messages
from forge.llm.base import CompletionResult
from forge.llm.fake import FakeLLM
from forge.store.config import ForgeConfig, ModelConfig

from tests.test_actor import make_actor, wait_idle


def user(seq: int, text: str, checkpoint: str = "cp1") -> UserMessage:
    return UserMessage(seq=seq, session_id="s", ts=float(seq), text=text,
                       workspace_checkpoint=checkpoint)


def rewind(seq: int, target: int, checkpoint: str = "cp1") -> HistoryRewound:
    return HistoryRewound(
        seq=seq, session_id="s", ts=float(seq), target_user_seq=target,
        target_checkpoint=checkpoint, safety_checkpoint="safety", replacement=False)


def test_active_events_support_nested_rewinds_and_keep_settings():
    events = [
        user(1, "one"),
        AssistantMessage(seq=2, session_id="s", ts=2, text="old one"),
        user(3, "two", "cp2"),
        AssistantMessage(seq=4, session_id="s", ts=4, text="old two"),
        rewind(5, 3, "cp2"),
        user(6, "two edited", "cp2"),
        AssistantMessage(seq=7, session_id="s", ts=7, text="new two"),
        AutonomyChanged(seq=8, session_id="s", ts=8, autonomy="guarded"),
        rewind(9, 1),
        user(10, "one edited"),
    ]
    active = active_events(events)
    assert [(e.seq, e.type) for e in active] == [
        (8, "autonomy_changed"), (10, "user_message")]
    messages = to_messages(events, "SYS")
    assert "one edited" in str(messages)
    assert "old one" not in str(messages)
    assert "new two" not in str(messages)


async def test_messages_capture_workspace_and_rewind_restores_exact_tree(tmp_path):
    actor, _ = make_actor(tmp_path, [
        CompletionResult(text="first"), CompletionResult(text="second")])
    work = tmp_path / "ws"
    (work / "base.bin").write_bytes(b"\x00before")
    await actor.post_message("first prompt")
    await wait_idle(actor)

    (work / "base.bin").write_bytes(b"\x00target")
    (work / "keep.txt").write_text("keep")
    await actor.post_message("second prompt")
    await wait_idle(actor)
    target = [e for e in actor.log.read() if e.type == "user_message"][-1]
    from forge.engine.projection import message_checkpoints
    assert message_checkpoints(actor.log.read()).get(target.seq)

    # These post-checkpoint edits belong to THIS session; record them as
    # controlled activity so the rewind gate treats them as its own (benign,
    # being rewound) rather than unexplained external drift that would block.
    ws = actor.shared_workspace
    before = ws.begin_tree()
    (work / "base.bin").write_bytes(b"changed")
    (work / "keep.txt").unlink()
    (work / "later.txt").write_text("later")
    ws.record_tree_change(before, origin="tool", action="write_file",
                          session_id=actor.meta.id)
    await actor.rewind(target.seq)

    assert (work / "base.bin").read_bytes() == b"\x00target"
    assert (work / "keep.txt").read_text() == "keep"
    assert not (work / "later.txt").exists()
    assert actor.meta.last_message_at is not None
    assert actor.log.read()[-1].type == "history_rewound"


async def test_edit_replacement_runs_from_active_branch_only(tmp_path):
    actor, llm = make_actor(tmp_path, [
        CompletionResult(text="old answer"),
        CompletionResult(text="discarded answer"),
        CompletionResult(text="replacement answer"),
    ])
    await actor.post_message("keep")
    await wait_idle(actor)
    await actor.post_message("discard me")
    await wait_idle(actor)
    target = [e for e in actor.log.read() if e.type == "user_message"][-1]

    await actor.rewind(target.seq, text="edited")
    await wait_idle(actor)

    final_context = str(llm.calls[-1])
    assert "keep" in final_context and "edited" in final_context
    assert "discard me" not in final_context
    assert "discarded answer" not in final_context
    seqs = [e.seq for e in actor.log.read()]
    assert seqs == sorted(seqs) and len(seqs) == len(set(seqs))


async def test_edit_resend_heals_dangling_before_target(tmp_path):
    # Repro: a prior run died leaving an unresolved tool_use, then the user
    # edits an EARLIER-or-equal message and resends. The rewind keeps the
    # orphan active (it precedes the target), so the resend's _run must heal it
    # before hitting the LLM — otherwise Anthropic 400s on the dangling call.
    from forge.engine.events import ToolCallSpec
    from forge.engine.projection import dangling_call_ids
    actor, llm = make_actor(tmp_path, [CompletionResult(text="resend answer")])
    # Orphan tool_use with no result, emitted before the target message.
    actor.emit(actor._e(
        AssistantMessage, text="", tool_calls=[
            ToolCallSpec(id="orphan", name="bash", arguments="{}")],
        usage_tokens=1))
    cp = actor.checkpoints.capture().id
    target = actor.emit(actor._e(
        UserMessage, text="original", workspace_checkpoint=cp))
    assert dangling_call_ids(actor.log.read()) == [("orphan", "bash")]

    await actor.rewind(target.seq, text="edited")
    await wait_idle(actor)

    assert dangling_call_ids(actor.log.read()) == []
    fin = next(e for e in actor.log.read()
               if e.type == "tool_call_finished" and e.call_id == "orphan")
    assert fin.is_error
    # The resend's LLM request was well-formed and used the edited text.
    assert "edited" in str(llm.calls[-1])


async def test_rewind_rejects_missing_inactive_and_legacy_targets(tmp_path):
    actor, _ = make_actor(tmp_path, [CompletionResult(text="ok")])
    with pytest.raises(RewindTargetMissing):
        await actor.rewind(999)

    await actor.post_message("current")
    await wait_idle(actor)
    target = next(e for e in actor.log.read() if e.type == "user_message")
    await actor.rewind(target.seq)
    with pytest.raises(RewindTargetInactive):
        await actor.rewind(target.seq)

    (tmp_path / "legacy").mkdir()
    actor2, _ = make_actor(tmp_path / "legacy", [])
    legacy = actor2.emit(actor2._e(UserMessage, text="old", workspace_checkpoint=None))
    with pytest.raises(RewindNoCheckpoint):
        await actor2.rewind(legacy.seq)


async def test_workspace_failure_is_a_typed_rewind_error(tmp_path, monkeypatch):
    actor, _ = make_actor(tmp_path, [CompletionResult(text="ok")])
    await actor.post_message("current")
    await wait_idle(actor)
    target = next(e for e in actor.log.read() if e.type == "user_message")

    def fail(_checkpoint_id):
        from forge.store.workspace_checkpoints import WorkspaceCheckpointError
        raise WorkspaceCheckpointError("tree missing")

    monkeypatch.setattr(actor.checkpoints, "restore", fail)
    with pytest.raises(RewindWorkspaceError, match="tree missing"):
        await actor.rewind(target.seq)
    assert not any(e.type == "history_rewound" for e in actor.log.read())


async def test_rewind_cancels_live_run(tmp_path):
    actor, _ = make_actor(tmp_path, [
        CompletionResult(text="first"), CompletionResult(text="slow")], delay=0.15)
    await actor.post_message("first")
    await wait_idle(actor)
    target = next(e for e in actor.log.read() if e.type == "user_message")
    await actor.post_message("running")
    await asyncio.sleep(0.02)
    await actor.rewind(target.seq)
    assert actor.meta.status == "idle"
    assert actor.run_task is None
    assert actor.log.read()[-1].type == "history_rewound"


def test_rehydrate_ignores_discarded_mid_run_and_restores_last_active_time(tmp_path):
    manager = SessionManager(tmp_path / "home", ForgeConfig(), FakeLLM([]), EventBus())
    actor = manager.create(cwd=str(tmp_path))
    checkpoint = actor.checkpoints.capture().id
    first = actor.emit(UserMessage(
        session_id=actor.meta.id, ts=10, text="kept", workspace_checkpoint=checkpoint))
    actor.emit(RunFinished(session_id=actor.meta.id, ts=11, reason="completed"))
    actor.emit(UserMessage(
        session_id=actor.meta.id, ts=20, text="discarded", workspace_checkpoint=checkpoint))
    actor.emit(HistoryRewound(
        session_id=actor.meta.id, ts=21, target_user_seq=first.seq + 2,
        target_checkpoint=checkpoint, safety_checkpoint=checkpoint, replacement=False))

    rehydrated = SessionManager(
        tmp_path / "home", ForgeConfig(), FakeLLM([]), EventBus())
    rehydrated.rehydrate()
    restored = rehydrated.get(actor.meta.id)
    assert restored.meta.last_message_at == 10
    assert restored.log.read()[-1].type == "history_rewound"


async def _build_two_message_session(tmp_path, work):
    """Create a session (via a manager so SessionCreated is emitted) with two
    user messages captured at distinct workspace states. Returns
    (home, session_id, target_user_msg, live_user_msg)."""
    home = tmp_path / "home"
    manager = SessionManager(home, ForgeConfig(), FakeLLM([
        CompletionResult(text="first"), CompletionResult(text="second")]), EventBus())
    actor = manager.create(cwd=str(work))
    (work / "f.txt").write_text("one")
    await actor.post_message("first")
    await wait_idle(actor)
    (work / "f.txt").write_text("two")
    await actor.post_message("second")
    await wait_idle(actor)
    from forge.engine.projection import message_checkpoints
    msgs = [e for e in actor.log.read() if e.type == "user_message"]
    cps = message_checkpoints(actor.log.read())
    # Stamp the resolved checkpoint id onto each returned message for tests that
    # simulate a mid-rewind crash (the capture now lands on a follow-up event).
    for m in msgs:
        m.workspace_checkpoint = cps.get(m.seq)
    return home, actor, msgs[0], msgs[1]


async def test_recover_crash_after_restore_before_marker(tmp_path):
    """Crash after the destructive restore but before the marker landed: the
    log still describes the old branch, so recovery restores the safety
    checkpoint and drops the intent (history stays on the old branch)."""
    from forge.store.rewind_intent import RewindIntent

    work = tmp_path / "ws"
    work.mkdir()
    home, actor, target, _ = await _build_two_message_session(tmp_path, work)
    last_seq = actor.log.last_seq

    # Simulate the crash: capture safety, write the intent, do the destructive
    # restore, but never append the marker.
    safety = actor.checkpoints.capture(label="pre-rewind").id
    actor.rewind_intent.write(RewindIntent(
        target_user_seq=target.seq, target_checkpoint=target.workspace_checkpoint,
        safety_checkpoint=safety, replacement=False))
    actor.checkpoints.restore(target.workspace_checkpoint)
    assert (work / "f.txt").read_text() == "one"  # restored to old target

    # Rehydrate a fresh manager over the same home.
    manager = SessionManager(home, ForgeConfig(), FakeLLM([]), EventBus())
    manager.rehydrate()
    restored = manager.get(actor.meta.id)
    assert restored.rewind_intent.read() is None
    assert (work / "f.txt").read_text() == "two"  # back on the live branch
    assert not any(e.type == "history_rewound" for e in restored.log.read())
    assert restored.log.last_seq == last_seq  # recovery appended nothing


async def test_recover_crash_after_marker_before_replacement(tmp_path):
    """Crash after the marker landed but before the replacement user message
    was appended: recovery restores the target checkpoint, appends the
    replacement, and mid-run detection marks it interrupted."""
    from forge.store.rewind_intent import RewindIntent

    work = tmp_path / "ws"
    work.mkdir()
    home, actor, target, _ = await _build_two_message_session(tmp_path, work)

    # Simulate: safety captured, intent written, restore done, marker emitted,
    # but crash before the replacement UserMessage was appended.
    safety = actor.checkpoints.capture(label="pre-rewind").id
    actor.rewind_intent.write(RewindIntent(
        target_user_seq=target.seq, target_checkpoint=target.workspace_checkpoint,
        safety_checkpoint=safety, replacement=True,
        replacement_text="edited prompt", replacement_images=[]))
    actor.checkpoints.restore(target.workspace_checkpoint)
    marker = actor.emit(HistoryRewound(
        session_id=actor.meta.id, ts=time.time(),
        target_user_seq=target.seq, target_checkpoint=target.workspace_checkpoint,
        safety_checkpoint=safety, replacement=True))

    manager = SessionManager(home, ForgeConfig(), FakeLLM([]), EventBus())
    manager.rehydrate()
    restored = manager.get(actor.meta.id)
    assert restored.rewind_intent.read() is None
    assert (work / "f.txt").read_text() == "one"  # restored target tree
    appended = [e for e in restored.log.read() if e.type == "user_message"
                and e.seq > marker.seq]
    assert len(appended) == 1 and appended[0].text == "edited prompt"
    # Mid-run detection closes the interrupted replacement run.
    assert restored.log.read()[-1].type == "run_finished"
    assert restored.log.read()[-1].reason == "interrupted"


def test_rewind_api_status_mapping_and_empty_replacement(tmp_path):
    cfg = ForgeConfig(models=[ModelConfig(id="m", display_name="m")], default_model="m")
    client = TestClient(create_app(tmp_path / "home", cfg, FakeLLM([
        CompletionResult(text="ok")
    ])))
    with client:
        sid = client.post("/api/sessions", json={"cwd": str(tmp_path)}).json()["id"]
        assert client.post(f"/api/sessions/{sid}/rewind",
                           json={"target_user_seq": 999}).status_code == 404
        client.post(f"/api/sessions/{sid}/messages", json={"text": "hello"})
        for _ in range(100):
            if client.get("/api/sessions").json()[0]["status"] == "idle":
                break
            time.sleep(0.01)
        target = next(e for e in client.get(
            f"/api/sessions/{sid}/events").json() if e["type"] == "user_message")
        response = client.post(f"/api/sessions/{sid}/rewind", json={
            "target_user_seq": target["seq"], "text": "", "images": []})
        assert response.status_code == 400
        assert client.post(f"/api/sessions/{sid}/rewind", json={
            "target_user_seq": target["seq"]}).status_code == 200
        assert client.post(f"/api/sessions/{sid}/rewind", json={
            "target_user_seq": target["seq"]}).status_code == 409
