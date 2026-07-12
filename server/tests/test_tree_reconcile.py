import json

from forge.engine.workspace import SharedWorkspace
from forge.store.workspace_activity import WorkspaceActivityStore


def _ws(tmp_path):
    d = tmp_path / "ws"
    d.mkdir(exist_ok=True)
    store = WorkspaceActivityStore(tmp_path / "home", d.resolve())
    return d, SharedWorkspace(d.resolve(), store)


def test_first_reconcile_seeds_no_external(tmp_path):
    d, ws = _ws(tmp_path)
    (d / "a.txt").write_text("hello")
    # First reconcile only seeds the cursor; the whole tree is not "external".
    assert ws.reconcile() is None
    assert not [r for r in ws.recent_activity() if r.origin == "external"]
    # Cursor file exists and is durable.
    assert ws._cursor_file.exists()
    assert json.loads(ws._cursor_file.read_text())["tree"]


def test_out_of_band_edit_records_once(tmp_path):
    d, ws = _ws(tmp_path)
    (d / "a.txt").write_text("v1")
    ws.reconcile()  # seed
    (d / "a.txt").write_text("v2")  # out-of-band edit
    rec = ws.reconcile()
    assert rec is not None and rec.origin == "external"
    assert str((d / "a.txt").resolve()) in rec.paths
    # A second reconcile with no further change records nothing (deduped).
    assert ws.reconcile() is None
    n = len([r for r in ws.recent_activity() if r.origin == "external"])
    assert n == 1


def test_controlled_edit_not_duplicated_by_reconcile(tmp_path):
    d, ws = _ws(tmp_path)
    f = d / "a.txt"
    f.write_text("v1")
    ws.reconcile()  # seed
    f.write_text("v2")
    ws.record_controlled_change(session_id="s1", action="write_file", paths=[f])
    # The controlled change advanced the cursor; reconcile must not relabel it.
    assert ws.reconcile() is None
    assert not [r for r in ws.recent_activity() if r.origin == "external"]


def test_terminal_launch_marker_claims_no_paths(tmp_path):
    d, ws = _ws(tmp_path)
    rec = ws.record_terminal_launch(session_id="s1", call_id="c1", note="t1")
    assert rec.origin == "terminal" and rec.action == "launch"
    assert rec.paths == []
    assert rec.session_id == "s1" and rec.call_id == "c1"


def test_terminal_async_write_seen_external_on_reconcile(tmp_path):
    d, ws = _ws(tmp_path)
    (d / "log.txt").write_text("start")
    ws.reconcile()  # seed
    ws.record_terminal_launch(session_id="s1", call_id="c1")
    # The terminal's async write lands after the marker; a later reconcile sees
    # it as external (the launch claimed no paths).
    (d / "log.txt").write_text("appended by bg process")
    rec = ws.reconcile()
    assert rec is not None and rec.origin == "external"
    assert str((d / "log.txt").resolve()) in rec.paths


def test_checkpoint_marker_records_user_seq_and_id(tmp_path):
    d, ws = _ws(tmp_path)
    rec = ws.record_checkpoint(session_id="s1", user_seq=7, checkpoint="cp3")
    assert rec.origin == "checkpoint" and rec.action == "checkpoint"
    assert "user_seq=7" in rec.note and "checkpoint=cp3" in rec.note
    assert rec.session_id == "s1"


def test_cursor_durable_across_restart(tmp_path):
    d, ws = _ws(tmp_path)
    (d / "a.txt").write_text("v1")
    ws.reconcile()  # seed + persist cursor
    tree = json.loads(ws._cursor_file.read_text())["tree"]
    # A fresh workspace over the same dir loads the persisted cursor tree, so the
    # tree objects referenced by the cursor still exist after "restart".
    store2 = WorkspaceActivityStore(tmp_path / "home", d.resolve())
    ws2 = SharedWorkspace(d.resolve(), store2)
    assert ws2._ensure_cursor(ws2._tracker_store()) == tree
    # No change since: reconcile is a no-op, no spurious external record.
    assert ws2.reconcile() is None


def test_tracker_unavailable_degrades_safely(tmp_path):
    d, ws = _ws(tmp_path)
    ws._tracker_failed = True  # simulate git unavailable
    (d / "a.txt").write_text("v1")
    assert ws.reconcile() is None
    assert ws.current_tree() is None
    assert ws.record_tree_change(None, origin="bash", action="bash") is None
