import os

import pytest

from forge.store.workspace_checkpoints import (
    WorkspaceCheckpointError, WorkspaceCheckpointStore,
)


def store(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    return WorkspaceCheckpointStore(tmp_path / "workspace.git", work), work


def test_capture_and_restore_roundtrip(tmp_path):
    s, work = store(tmp_path)
    (work / "a.txt").write_text("one\n")
    (work / "sub").mkdir()
    (work / "sub" / "b.txt").write_text("two\n")
    cp = s.capture("initial")
    assert cp.id == "cp1" and cp.tree

    # mutate: modify, delete, create
    (work / "a.txt").write_text("changed\n")
    (work / "sub" / "b.txt").unlink()
    (work / "c.txt").write_text("new\n")

    s.restore(cp.id)
    assert (work / "a.txt").read_text() == "one\n"
    assert (work / "sub" / "b.txt").read_text() == "two\n"
    assert not (work / "c.txt").exists()


def test_binary_and_symlink_captured(tmp_path):
    s, work = store(tmp_path)
    (work / "data.bin").write_bytes(b"\x00\x01\x02binary\xff")
    (work / "target.txt").write_text("target\n")
    os.symlink("target.txt", work / "link")
    cp = s.capture()

    (work / "data.bin").write_bytes(b"junk")
    (work / "link").unlink()
    s.restore(cp.id)

    assert (work / "data.bin").read_bytes() == b"\x00\x01\x02binary\xff"
    assert (work / "link").is_symlink()
    assert os.readlink(work / "link") == "target.txt"


def test_dedup_identical_trees(tmp_path):
    s, work = store(tmp_path)
    (work / "a.txt").write_text("x\n")
    cp1 = s.capture()
    cp2 = s.capture()  # nothing changed
    assert cp1.id == cp2.id
    assert len(s.list()) == 1

    (work / "a.txt").write_text("y\n")
    cp3 = s.capture()
    assert cp3.id != cp1.id and len(s.list()) == 2


def test_hard_excludes_not_captured(tmp_path):
    s, work = store(tmp_path)
    (work / "keep.txt").write_text("keep\n")
    (work / ".git").mkdir()
    (work / ".git" / "HEAD").write_text("ref\n")
    (work / "node_modules").mkdir()
    (work / "node_modules" / "dep.js").write_text("dep\n")
    (work / ".forge").mkdir()
    (work / ".forge" / "state").write_text("s\n")
    cp = s.capture()

    # excluded dirs left untouched by a restore that would otherwise remove
    # anything not in the tree
    (work / "extra.txt").write_text("e\n")
    s.restore(cp.id)
    assert (work / ".git" / "HEAD").exists()
    assert (work / "node_modules" / "dep.js").exists()
    assert (work / ".forge" / "state").exists()
    assert not (work / "extra.txt").exists()
    assert (work / "keep.txt").exists()


def test_honors_project_gitignore(tmp_path):
    s, work = store(tmp_path)
    (work / ".gitignore").write_text("secret.txt\nbuildout/\n")
    (work / "keep.txt").write_text("k\n")
    (work / "secret.txt").write_text("shh\n")
    (work / "buildout").mkdir()
    (work / "buildout" / "o.txt").write_text("o\n")
    cp = s.capture()

    tree = s._git("ls-tree", "-r", "--name-only", cp.tree).splitlines()
    assert "keep.txt" in tree
    assert "secret.txt" not in tree
    assert not any(p.startswith("buildout") for p in tree)


def test_does_not_touch_project_repo(tmp_path):
    s, work = store(tmp_path)
    # A real project git repo in the work tree must be left completely alone.
    import subprocess
    env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_GLOBAL": os.devnull}
    subprocess.run(["git", "init", "-q"], cwd=work, env=env, check=True)
    (work / "tracked.txt").write_text("t\n")
    head_before = (work / ".git" / "HEAD").read_text()
    assert not (work / ".git" / "index").exists()

    s.capture()
    (work / "tracked.txt").write_text("changed\n")
    s.restore("cp1")

    assert (work / ".git" / "HEAD").read_text() == head_before
    # our capture used a private index; project index was never created
    assert not (work / ".git" / "index").exists()


def test_unknown_checkpoint_raises(tmp_path):
    s, _ = store(tmp_path)
    with pytest.raises(KeyError):
        s.restore("cp99")


def test_missing_tree_object_raises(tmp_path):
    s, work = store(tmp_path)
    (work / "a.txt").write_text("x\n")
    cp = s.capture()
    # corrupt the recorded tree sha; restore must fail loudly, not silently
    cp.tree = "0" * 40
    with pytest.raises(WorkspaceCheckpointError):
        s.restore(cp.id)


def test_persists_across_instances(tmp_path):
    s, work = store(tmp_path)
    (work / "a.txt").write_text("one\n")
    cp = s.capture("saved")

    s2 = WorkspaceCheckpointStore(tmp_path / "workspace.git", work)
    assert [c.id for c in s2.list()] == [cp.id]
    (work / "a.txt").write_text("two\n")
    s2.restore(cp.id)
    assert (work / "a.txt").read_text() == "one\n"
