from forge.tools.base import ToolContext, openai_spec, truncate_middle
from forge.tools.files_read import ReadFileTool
from forge.tools.search import GlobTool, GrepTool, ListDirTool


def ctx(tmp_path):
    return ToolContext(cwd=tmp_path)


async def test_read_file_numbers_lines(tmp_path):
    (tmp_path / "a.txt").write_text("alpha\nbeta\n")
    r = await ReadFileTool().run({"path": "a.txt"}, ctx(tmp_path))
    assert not r.is_error and r.output == "     1\talpha\n     2\tbeta"


async def test_read_file_missing_is_error(tmp_path):
    r = await ReadFileTool().run({"path": "nope.txt"}, ctx(tmp_path))
    assert r.is_error and "not found" in r.output.lower()


async def test_glob_grep_listdir(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "x.py").write_text("def needle(): pass\n")
    (tmp_path / "y.md").write_text("nothing\n")
    g = await GlobTool().run({"pattern": "**/*.py"}, ctx(tmp_path))
    assert "pkg/x.py" in g.output
    s = await GrepTool().run({"pattern": "needle"}, ctx(tmp_path))
    assert "x.py" in s.output and "1" in s.output
    ls = await ListDirTool().run({}, ctx(tmp_path))
    assert "pkg/" in ls.output and "y.md" in ls.output


def test_spec_and_truncation():
    spec = openai_spec(ReadFileTool())
    assert spec["type"] == "function" and spec["function"]["name"] == "read_file"
    long = "x" * 50_000
    t = truncate_middle(long, max_chars=1000)
    assert len(t) < 1200 and "truncated" in t


def test_display():
    assert ReadFileTool().display({"path": "a.txt"}) == "a.txt"
