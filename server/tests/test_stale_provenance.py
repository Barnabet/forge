from pathlib import Path

from starlette.testclient import TestClient

from forge.api.app import create_app
from forge.engine.workspace import SharedWorkspace, hash_text
from forge.llm.fake import FakeLLM
from forge.store.changesets import ChangesetStore, RevertConflict
from forge.store.config import ForgeConfig, ModelConfig
from forge.store.workspace_activity import WorkspaceActivityStore


def _ws(tmp_path):
    d = tmp_path / "ws"
    d.mkdir(exist_ok=True)
    store = WorkspaceActivityStore(tmp_path / "home", d.resolve())
    return d, SharedWorkspace(d.resolve(), store)


# -- stale-write guard ------------------------------------------------------

def test_foreign_session_stale_overwrite_names_author(tmp_path):
    d, ws = _ws(tmp_path)
    f = d / "file.txt"
    f.write_text("v1")
    ws.observe("s1", f)  # s1 read v1
    # A different session writes v2 through the controlled path.
    f.write_text("v2")
    ws.record_controlled_change(session_id="s2", action="write_file", paths=[f])
    # s1's write is now stale; message names s2 as author.
    msg = ws.detect_stale("s1", f)
    assert msg is not None and "session s2" in msg


def test_reread_clears_stale(tmp_path):
    d, ws = _ws(tmp_path)
    f = d / "file.txt"
    f.write_text("v1")
    ws.observe("s1", f)
    f.write_text("v2")
    ws.record_controlled_change(session_id="s2", action="write_file", paths=[f])
    assert ws.detect_stale("s1", f) is not None
    ws.observe("s1", f)  # s1 re-reads
    assert ws.detect_stale("s1", f) is None


def test_external_edit_reports_external_author_and_dedupes(tmp_path):
    d, ws = _ws(tmp_path)
    f = d / "file.txt"
    f.write_text("v1")
    ws.observe("s1", f)
    f.write_text("hand-edited")  # unexplained drift
    msg = ws.detect_stale("s1", f)
    assert msg is not None and "external" in msg
    # One external observation recorded; a second detect_stale doesn't dup it.
    n = len([r for r in ws.recent_activity() if r.origin == "external"])
    assert n == 1
    ws.detect_stale("s1", f)
    n2 = len([r for r in ws.recent_activity() if r.origin == "external"])
    assert n2 == 1


def test_no_baseline_allows_write(tmp_path):
    d, ws = _ws(tmp_path)
    f = d / "file.txt"
    f.write_text("v1")
    assert ws.detect_stale("s1", f) is None  # never observed


def test_observe_hash_matches_hash_text(tmp_path):
    d, ws = _ws(tmp_path)
    f = d / "file.txt"
    f.write_text("body")
    ws.observe_hash("s1", f, hash_text("body"))
    assert ws.detect_stale("s1", f) is None


# -- changeset revert -------------------------------------------------------

def _cs(tmp_path):
    root = tmp_path / "cs"
    root.mkdir()
    return ChangesetStore(root)


def test_ordinary_revert_returns_provenance(tmp_path):
    store = _cs(tmp_path)
    f = tmp_path / "t.txt"
    f.write_text("new")
    cs = store.record(f, "old", "new", session_id="s1", call_id="c1",
                      before_hash=hash_text("old"), after_hash=hash_text("new"))
    info = store.revert(cs.index)
    assert f.read_text() == "old"
    assert info["session_id"] == "s1" and info["call_id"] == "c1"
    assert info["before_hash"] == hash_text("new")
    assert info["after_hash"] == hash_text("old")
    assert store.list()[cs.index].status == "reverted"


def test_conflicting_revert_raises_and_leaves_disk_untouched(tmp_path):
    store = _cs(tmp_path)
    f = tmp_path / "t.txt"
    f.write_text("new")
    cs = store.record(f, "old", "new", after_hash=hash_text("new"))
    f.write_text("someone else edited")  # drift
    try:
        store.revert(cs.index)
        raise AssertionError("expected RevertConflict")
    except RevertConflict:
        pass
    assert f.read_text() == "someone else edited"  # untouched
    assert store.list()[cs.index].status == "pending"  # status untouched


def test_legacy_changeset_without_hashes_reverts(tmp_path):
    store = _cs(tmp_path)
    f = tmp_path / "t.txt"
    f.write_text("new")
    # Legacy record: no hashes supplied. Revert falls back to hashing the blob.
    cs = store.record(f, "old", "new")
    assert cs.before_hash is None and cs.after_hash is None
    info = store.revert(cs.index)
    assert f.read_text() == "old"
    assert info["before_hash"] == hash_text("new")


# -- FS API provenance ------------------------------------------------------

def _client(tmp_path):
    cfg = ForgeConfig(models=[ModelConfig(id="m", display_name="m")],
                      default_model="m", max_concurrent=3)
    app = create_app(home=tmp_path / "home", config=cfg, llm=FakeLLM([]))
    return TestClient(app)


def _activity(tmp_path, ws):
    return WorkspaceActivityStore(tmp_path / "home", Path(ws).resolve()).read()


def test_fs_mutations_record_provenance(tmp_path):
    ws = tmp_path / "proj"
    ws.mkdir()
    (ws / "src.txt").write_text("x")
    client = _client(tmp_path)
    with client:
        sid = client.post("/api/sessions", json={"cwd": str(ws)}).json()["id"]
        client.post(f"/api/sessions/{sid}/fs/mkdir", json={"path": "sub"})
        client.post(f"/api/sessions/{sid}/fs/touch", json={"path": "sub/n.txt"})
        client.post(f"/api/sessions/{sid}/fs/move",
                    json={"src": "src.txt", "dst": "sub/moved.txt"})
        client.post(f"/api/sessions/{sid}/fs/upload", data={"dir": "sub"},
                    files=[("files", ("u.txt", b"up", "text/plain"))])
        client.post(f"/api/sessions/{sid}/fs/delete", json={"path": "sub/n.txt"})
    recs = _activity(tmp_path, ws)
    actions = [r.action for r in recs]
    assert {"mkdir", "touch", "move", "upload", "delete"} <= set(actions)
    assert all(r.origin == "fs_api" for r in recs if r.action in
               {"mkdir", "touch", "move", "upload", "delete"})
    assert all(r.session_id == sid for r in recs)
    # move before/after coherent: src removed (after None), dst created.
    mv = next(r for r in recs if r.action == "move")
    src_key = str((ws / "src.txt").resolve())
    dst_key = str((ws / "sub" / "moved.txt").resolve())
    assert mv.after[src_key] is None
    assert mv.after[dst_key] is not None


def test_fs_file_read_observes_exact_bytes(tmp_path):
    ws = tmp_path / "proj"
    ws.mkdir()
    (ws / "f.txt").write_text("hello")
    client = _client(tmp_path)
    with client:
        sid = client.post("/api/sessions", json={"cwd": str(ws)}).json()["id"]
        r = client.get(f"/api/sessions/{sid}/fs/file", params={"path": "f.txt"})
        assert r.content == b"hello"
        actor = client.app.state.manager.get(sid)
        shared = actor.shared_workspace
        assert shared.baseline(sid, ws / "f.txt") == hash_text("hello")
        assert shared.detect_stale(sid, ws / "f.txt") is None
