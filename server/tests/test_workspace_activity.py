import os

from forge.engine.bus import EventBus
from forge.engine.manager import SessionManager
from forge.engine.workspace import (
    SharedWorkspace, WorkspaceRegistry, _hash_path,
)
from forge.llm.fake import FakeLLM
from forge.store.config import ForgeConfig
from forge.store.workspace_activity import (
    WorkspaceActivityStore, workspace_hash,
)


# -- store: append / order / reload -----------------------------------------

def test_activity_append_order_and_reload(tmp_path):
    home = tmp_path / "home"
    cwd = tmp_path / "ws"
    cwd.mkdir()
    store = WorkspaceActivityStore(home, cwd.resolve())
    r1 = store.append(origin="tool", action="write_file", paths=["a.txt"])
    r2 = store.append(origin="fs_api", action="edit", paths=["b.txt"],
                      session_id="s1")
    r3 = store.append(origin="external", action="observed", paths=["c.txt"])
    assert [r.seq for r in (r1, r2, r3)] == [1, 2, 3]

    # Fresh store over the same path reloads in order with monotonic seq intact.
    reloaded = WorkspaceActivityStore(home, cwd.resolve())
    recs = reloaded.read()
    assert [r.seq for r in recs] == [1, 2, 3]
    assert [r.action for r in recs] == ["write_file", "edit", "observed"]
    assert recs[1].session_id == "s1"
    # Continuing appends keep monotonic seq across the reload.
    r4 = reloaded.append(origin="tool", action="more")
    assert r4.seq == 4


def test_activity_read_after_seq_and_recent(tmp_path):
    store = WorkspaceActivityStore(tmp_path / "home", (tmp_path / "ws").resolve())
    for i in range(5):
        store.append(origin="tool", action=f"a{i}")
    assert [r.action for r in store.read(after_seq=3)] == ["a3", "a4"]
    assert [r.action for r in store.recent(limit=2)] == ["a3", "a4"]


def test_activity_missing_and_malformed_load_cleanly(tmp_path):
    store = WorkspaceActivityStore(tmp_path / "home", (tmp_path / "ws").resolve())
    # Missing log reads as empty.
    assert store.read() == []
    store.append(origin="tool", action="ok")
    # A corrupt line is tolerated and skipped.
    with store.path.open("a") as f:
        f.write("this is not json\n")
    store.append(origin="tool", action="ok2")
    assert [r.action for r in store.read()] == ["ok", "ok2"]


def test_activity_durable_fsync(tmp_path):
    # Append must be durable: content is on disk immediately after return.
    store = WorkspaceActivityStore(tmp_path / "home", (tmp_path / "ws").resolve())
    store.append(origin="tool", action="durable")
    assert store.path.exists()
    assert "durable" in store.path.read_text()


def test_workspace_hash_stable_and_alias(tmp_path):
    real = (tmp_path / "real").resolve()
    assert workspace_hash(real) == workspace_hash(real)
    assert workspace_hash(real) != workspace_hash((tmp_path / "other").resolve())


# -- registry: shared lock by resolved cwd ----------------------------------

def test_registry_same_cwd_shares_object(tmp_path):
    reg = WorkspaceRegistry(tmp_path / "home")
    ws = tmp_path / "proj"
    ws.mkdir()
    a = reg.get(str(ws))
    b = reg.get(str(ws) + "/.")  # different string, same resolved dir
    assert a is b
    assert a.lock is b.lock


def test_registry_different_cwd_different_lock(tmp_path):
    reg = WorkspaceRegistry(tmp_path / "home")
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    a = reg.get(str(tmp_path / "one"))
    b = reg.get(str(tmp_path / "two"))
    assert a is not b
    assert a.lock is not b.lock


def test_registry_symlink_alias_shares_lock(tmp_path):
    reg = WorkspaceRegistry(tmp_path / "home")
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    try:
        os.symlink(real, link)
    except (OSError, NotImplementedError):
        return  # platform without symlink support: skip
    a = reg.get(str(real))
    b = reg.get(str(link))
    assert a is b
    assert a.lock is b.lock


# -- baseline observation ---------------------------------------------------

def test_hash_missing_vs_empty(tmp_path):
    missing = tmp_path / "nope.txt"
    empty = tmp_path / "empty.txt"
    empty.write_text("")
    assert _hash_path(missing) is None
    assert _hash_path(empty) is not None


def test_baseline_observe_and_current(tmp_path):
    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()
    store = WorkspaceActivityStore(tmp_path / "home", ws_dir.resolve())
    ws = SharedWorkspace(ws_dir.resolve(), store)
    f = ws_dir / "file.txt"

    # Missing before creation.
    assert ws.observe("s1", f) is None
    assert ws.has_baseline("s1", f)
    f.write_text("hello")
    # Current reflects new content, but baseline is still what s1 last observed.
    assert ws.current_hash(f) is not None
    assert ws.baseline("s1", f) is None
    # Re-observe updates the baseline.
    h = ws.observe("s1", f)
    assert h == ws.current_hash(f)
    # Different sessions track independently.
    assert not ws.has_baseline("s2", f)


def test_record_controlled_change_updates_baseline_and_log(tmp_path):
    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()
    store = WorkspaceActivityStore(tmp_path / "home", ws_dir.resolve())
    ws = SharedWorkspace(ws_dir.resolve(), store)
    f = ws_dir / "file.txt"
    ws.observe("s1", f)  # baseline: missing
    f.write_text("new content")
    rec = ws.record_controlled_change(
        session_id="s1", action="write_file", paths=[f], call_id="c1")
    key = str(f.resolve())
    assert rec.origin == "tool"
    assert rec.before[key] is None
    assert rec.after[key] == ws.current_hash(f)
    # Baseline refreshed to the new content.
    assert ws.baseline("s1", f) == ws.current_hash(f)
    # Persisted.
    assert store.read()[-1].call_id == "c1"


def test_baseline_key_stable_across_create_delete_under_symlink(tmp_path):
    # A path's baseline key must stay stable whether or not the leaf exists, and
    # whether reached via a symlinked ancestor alias, so observe-then-delete
    # leaves the baseline addressable by the same logical path.
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    try:
        os.symlink(real, link)
    except (OSError, NotImplementedError):
        return  # platform without symlink support: skip

    store = WorkspaceActivityStore(tmp_path / "home", real.resolve())
    ws = SharedWorkspace(real.resolve(), store)

    f_real = real / "file.txt"
    f_link = link / "file.txt"  # same logical path via symlinked ancestor
    f_real.write_text("hello")

    # Observe the existing file via the real path.
    h = ws.observe("s1", f_real)
    assert h is not None
    assert ws.has_baseline("s1", f_real)
    # Reached via the symlink alias it is the same baseline.
    assert ws.has_baseline("s1", f_link)
    assert ws.baseline("s1", f_link) == h

    # Delete the file; the baseline key must not shift now that the leaf is gone.
    f_real.unlink()
    assert ws.has_baseline("s1", f_real)
    assert ws.has_baseline("s1", f_link)
    assert ws.baseline("s1", f_real) == h
    assert ws.baseline("s1", f_link) == h


def test_record_external_change(tmp_path):
    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()
    store = WorkspaceActivityStore(tmp_path / "home", ws_dir.resolve())
    ws = SharedWorkspace(ws_dir.resolve(), store)
    f = ws_dir / "file.txt"
    f.write_text("outside change")
    rec = ws.record_external_change(action="modified", paths=[f], note="editor")
    assert rec.origin == "external"
    assert rec.note == "editor"
    assert ws.recent_activity()[-1].seq == rec.seq


# -- manager / actor integration: shared lock -------------------------------

def _mgr(tmp_path, home_name="home"):
    return SessionManager(home=tmp_path / home_name, config=ForgeConfig(),
                          llm=FakeLLM([]), bus=EventBus())


def test_same_cwd_actors_share_lock_under_one_manager(tmp_path):
    mgr = _mgr(tmp_path)
    cwd = tmp_path / "proj"
    cwd.mkdir()
    a = mgr.create(cwd=str(cwd))
    b = mgr.create(cwd=str(cwd) + "/.")  # alias of the same resolved dir
    assert a.workspace_lock is b.workspace_lock
    assert a.shared_workspace is b.shared_workspace


def test_different_cwd_actors_have_different_lock(tmp_path):
    mgr = _mgr(tmp_path)
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    a = mgr.create(cwd=str(tmp_path / "one"))
    b = mgr.create(cwd=str(tmp_path / "two"))
    assert a.workspace_lock is not b.workspace_lock


def test_two_managers_same_home_share_via_default_registry(tmp_path):
    # Direct construction path: managers each own a registry, but they share the
    # same home. The manager injects, so two managers with distinct registries
    # would NOT share. This asserts the fallback default registry (used when no
    # workspace is injected) shares by home + resolved cwd.
    from forge.engine.actor import SessionActor, SessionMeta
    from forge.engine.scheduler import Scheduler

    home = tmp_path / "home"
    cwd = tmp_path / "proj"
    cwd.mkdir()

    def build(sid):
        meta = SessionMeta(id=sid, cwd=str(cwd), model="m")
        return SessionActor(
            meta=meta, home=home, config=ForgeConfig(), llm=FakeLLM([]),
            bus=EventBus(), scheduler=Scheduler(3),
            system_prompt_fn=lambda m: "SYS")

    a = build("a1")
    b = build("b1")
    assert a.workspace_lock is b.workspace_lock
