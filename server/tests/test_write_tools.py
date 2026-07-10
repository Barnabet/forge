from forge.store.changesets import ChangesetStore
from forge.tools.base import ToolContext
from forge.tools.files_write import EditFileTool, WriteFileTool


def ctx(tmp_path):
    return ToolContext(cwd=tmp_path / "ws",
                       changesets=ChangesetStore(tmp_path / "cs"))


async def test_write_then_edit_records_changesets(tmp_path):
    (tmp_path / "ws").mkdir()
    c = ctx(tmp_path)
    r1 = await WriteFileTool().run({"path": "a.py", "content": "x = 1\n"}, c)
    assert not r1.is_error and r1.diff_stats.added == 1 and r1.diff_stats.removed == 0
    r2 = await EditFileTool().run(
        {"path": "a.py", "old_string": "x = 1", "new_string": "x = 2"}, c)
    assert not r2.is_error
    assert (tmp_path / "ws" / "a.py").read_text() == "x = 2\n"
    sets = c.changesets.list()
    assert len(sets) == 2 and sets[1].added == 1 and sets[1].removed == 1
    assert "-x = 1" in sets[1].diff and "+x = 2" in sets[1].diff


async def test_edit_requires_unique_match(tmp_path):
    (tmp_path / "ws").mkdir()
    c = ctx(tmp_path)
    (tmp_path / "ws" / "b.py").write_text("y = 0\ny = 0\n")
    r = await EditFileTool().run(
        {"path": "b.py", "old_string": "y = 0", "new_string": "y = 9"}, c)
    assert r.is_error and "2 times" in r.output
    r2 = await EditFileTool().run(
        {"path": "b.py", "old_string": "y = 0", "new_string": "y = 9",
         "replace_all": True}, c)
    assert not r2.is_error
    assert (tmp_path / "ws" / "b.py").read_text() == "y = 9\ny = 9\n"


async def test_revert_and_keep_all(tmp_path):
    (tmp_path / "ws").mkdir()
    c = ctx(tmp_path)
    await WriteFileTool().run({"path": "new.txt", "content": "hello\n"}, c)
    c.changesets.revert(0)
    assert not (tmp_path / "ws" / "new.txt").exists()
    assert c.changesets.get(0).status == "reverted"
    await WriteFileTool().run({"path": "k.txt", "content": "keep\n"}, c)
    c.changesets.keep_all()
    assert c.changesets.get(1).status == "kept"
