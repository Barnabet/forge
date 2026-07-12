"""Rewind conflict-gate tests. These drive SessionActor._rewind_conflicts with a
real SharedWorkspace and checkpoint store so the gate's tree-diff + activity-scan
logic is exercised deterministically without a live LLM run."""
import pytest

from forge.engine.actor import RewindProvenanceUnavailable
from forge.engine.projection import message_activity_boundaries
from forge.engine.events import MessageCheckpointed
from forge.store.workspace_checkpoints import WorkspaceCheckpointError

from tests.test_actor import make_actor, wait_idle
from forge.llm.base import CompletionResult


def _capture(actor, user_seq):
    """Capture a message checkpoint the way post_message does and return
    (checkpoint_id, events) with a MessageCheckpointed carrying the boundary."""
    cp, activity_seq = actor._capture_message_checkpoint(user_seq)
    ev = MessageCheckpointed(
        seq=user_seq + 1, session_id=actor.meta.id, ts=float(user_seq),
        user_seq=user_seq, checkpoint=cp, workspace_activity_seq=activity_seq)
    return cp, [ev]


def _rel(actor, name):
    return name


async def test_same_path_edit_by_other_session_blocks(tmp_path):
    actor, _ = make_actor(tmp_path, [])
    ws = actor.shared_workspace
    d = ws.cwd
    f = d / "shared.txt"
    f.write_text("v1")
    cp, events = _capture(actor, 1)
    # Another session edits the same path after the checkpoint.
    before = ws.begin_tree()
    f.write_text("v2-by-B")
    ws.record_tree_change(before, origin="tool", action="write_file",
                          session_id="other")
    conflicts = actor._rewind_conflicts(cp, 1, events)
    assert conflicts, "expected a conflict on the shared path"
    assert any(a == "session other" for _, a in conflicts)
    # Content preserved (gate does not restore).
    assert f.read_text() == "v2-by-B"


async def test_external_same_path_blocks(tmp_path):
    actor, _ = make_actor(tmp_path, [])
    ws = actor.shared_workspace
    f = ws.cwd / "shared.txt"
    f.write_text("v1")
    cp, events = _capture(actor, 1)
    # Out-of-band edit surfaces as external on reconcile inside the gate.
    f.write_text("v2-external")
    conflicts = actor._rewind_conflicts(cp, 1, events)
    assert conflicts
    assert any(a == "external" for _, a in conflicts)
    assert f.read_text() == "v2-external"


async def test_foreign_addition_blocks(tmp_path):
    actor, _ = make_actor(tmp_path, [])
    ws = actor.shared_workspace
    d = ws.cwd
    (d / "mine.txt").write_text("v1")
    cp, events = _capture(actor, 1)
    # Another session ADDS a new file absent from the target tree. The restore
    # reinstates the exact target tree and would delete it, so it must block.
    other = d / "theirs.txt"
    before = ws.begin_tree()
    other.write_text("added by B")
    ws.record_tree_change(before, origin="tool", action="write_file",
                          session_id="other")
    conflicts = actor._rewind_conflicts(cp, 1, events)
    assert conflicts, "foreign addition is deleted by restore and must block"
    assert any(a == "session other" for _, a in conflicts)
    assert other.read_text() == "added by B"  # preserved (gate does not restore)


async def test_unrelated_unchanged_path_record_allows(tmp_path):
    actor, _ = make_actor(tmp_path, [])
    ws = actor.shared_workspace
    d = ws.cwd
    (d / "mine.txt").write_text("v1")
    cp, events = _capture(actor, 1)
    # A foreign provenance record naming a path that did NOT actually change
    # between target and current: the restore touches nothing, so no conflict.
    ws.activity.append(origin="tool", action="write_file",
                       paths=[str(d / "phantom.txt")], session_id="other")
    conflicts = actor._rewind_conflicts(cp, 1, events)
    assert conflicts == [], f"unchanged path should not conflict: {conflicts}"


async def test_own_edit_rewind_succeeds(tmp_path):
    actor, _ = make_actor(tmp_path, [])
    ws = actor.shared_workspace
    f = ws.cwd / "mine.txt"
    f.write_text("v1")
    cp, events = _capture(actor, 1)
    # This same session edits the path after the checkpoint: benign, being rewound.
    before = ws.begin_tree()
    f.write_text("v2-by-me")
    ws.record_tree_change(before, origin="tool", action="write_file",
                          session_id=actor.meta.id)
    conflicts = actor._rewind_conflicts(cp, 1, events)
    assert conflicts == [], f"own edit must not block: {conflicts}"


async def test_legacy_none_boundary_scans_whole_log(tmp_path):
    actor, _ = make_actor(tmp_path, [])
    ws = actor.shared_workspace
    f = ws.cwd / "shared.txt"
    f.write_text("v1")
    cp, _ = _capture(actor, 1)
    # Legacy MessageCheckpointed with no boundary → None → conservative scan.
    ev = MessageCheckpointed(seq=2, session_id=actor.meta.id, ts=1.0,
                             user_seq=1, checkpoint=cp)
    events = [ev]
    assert message_activity_boundaries(events).get(1) is None
    f.write_text("v2-external")
    conflicts = actor._rewind_conflicts(cp, 1, events)
    assert conflicts and any(a == "external" for _, a in conflicts)


async def test_tracker_unavailable_still_blocks_foreign_edit(tmp_path):
    """Even with the shared tree tracker disabled, the gate computes touched
    paths from the SESSION checkpoint store, so a foreign edit still blocks."""
    actor, _ = make_actor(tmp_path, [])
    ws = actor.shared_workspace
    f = ws.cwd / "shared.txt"
    f.write_text("v1")
    cp, events = _capture(actor, 1)
    # A foreign edit is recorded to the activity log directly, then the shared
    # tracker is disabled so it cannot compute any tree diff.
    f.write_text("v2-by-B")
    ws.activity.append(origin="tool", action="write_file",
                       paths=[str(ws.canonical(f))], session_id="other")
    ws._tracker = None
    ws._tracker_failed = True  # shared tracker unavailable
    conflicts = actor._rewind_conflicts(cp, 1, events)
    assert conflicts, "session-store diff must still detect the touched path"
    assert any(a == "session other" for _, a in conflicts)
    assert f.read_text() == "v2-by-B"  # preserved (gate does not restore)


async def test_session_diff_failure_fails_closed(tmp_path):
    """If the session checkpoint store cannot snapshot/diff the live tree, the
    gate raises RewindProvenanceUnavailable rather than proceeding."""
    actor, _ = make_actor(tmp_path, [])
    ws = actor.shared_workspace
    f = ws.cwd / "shared.txt"
    f.write_text("v1")
    cp, events = _capture(actor, 1)

    def boom():
        raise WorkspaceCheckpointError("git broken")

    actor.checkpoints.snapshot_tree = boom
    with pytest.raises(RewindProvenanceUnavailable):
        actor._rewind_conflicts(cp, 1, events)


async def test_missing_target_tree_fails_closed(tmp_path):
    actor, _ = make_actor(tmp_path, [])
    ws = actor.shared_workspace
    (ws.cwd / "a.txt").write_text("v1")
    cp, events = _capture(actor, 1)
    with pytest.raises(RewindProvenanceUnavailable):
        actor._rewind_conflicts("cp-does-not-exist", 1, events)


async def test_rewind_fails_closed_preserves_files(tmp_path):
    """End-to-end: a session-store diff failure makes rewind() raise and leaves
    the working tree untouched (no restore)."""
    actor, _ = make_actor(tmp_path, [CompletionResult(text="ok")])
    await actor.post_message("first")
    await wait_idle(actor)
    f = actor.shared_workspace.cwd / "work.txt"
    f.write_text("live-content")
    target = next(e for e in actor.log.read() if e.type == "user_message")

    def boom():
        raise WorkspaceCheckpointError("git broken")

    actor.checkpoints.snapshot_tree = boom
    with pytest.raises(RewindProvenanceUnavailable):
        await actor.rewind(target.seq)
    assert f.read_text() == "live-content"
    assert not any(e.type == "history_rewound" for e in actor.log.read())
