import json

from forge.engine.fileindex import (
    DEFAULT_THRESHOLD, FileIndex, chunk_file, extract_text,
    list_project_files, project_file_index_path,
)
from forge.llm.embeddings import FakeEmbedder


# -- list_project_files --------------------------------------------------------

def test_list_project_files_excludes_hard_dirs(tmp_path):
    (tmp_path / "keep.txt").write_text("hi")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.js").write_text("x")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "lib.py").write_text("y")
    files = list_project_files(tmp_path, max_bytes=10_000, max_files=100)
    names = {p.name for p in files}
    assert "keep.txt" in names
    assert "dep.js" not in names and "lib.py" not in names


def test_list_project_files_respects_max_bytes(tmp_path):
    (tmp_path / "small.txt").write_text("ok")
    (tmp_path / "big.txt").write_text("x" * 5000)
    files = list_project_files(tmp_path, max_bytes=1000, max_files=100)
    names = {p.name for p in files}
    assert "small.txt" in names and "big.txt" not in names


def test_list_project_files_caps_at_max_files(tmp_path):
    for i in range(10):
        (tmp_path / f"f{i}.txt").write_text("data")
    files = list_project_files(tmp_path, max_bytes=10_000, max_files=3)
    assert len(files) == 3


# -- extract_text --------------------------------------------------------------

def test_extract_text_plain(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("hello world")
    assert extract_text(p) == "hello world"


def test_extract_text_binary_returns_none(tmp_path):
    p = tmp_path / "b.bin"
    p.write_bytes(b"\xff\xfe\x00")
    assert extract_text(p) is None


def test_extract_text_empty_returns_none(tmp_path):
    p = tmp_path / "e.txt"
    p.write_text("   \n\n  ")
    assert extract_text(p) is None


# -- chunk_file ----------------------------------------------------------------

def test_chunk_file_short_one_chunk():
    chunks = chunk_file("line one\nline two\nline three")
    assert len(chunks) == 1
    assert chunks[0].start_line == 1 and chunks[0].end_line == 3
    assert chunks[0].text == "line one\nline two\nline three"


def test_chunk_file_empty():
    assert chunk_file("") == []
    assert chunk_file("\n\n\n") == []


def test_chunk_file_splits_and_contiguous_line_numbers():
    text = "\n".join(f"line {i}" for i in range(100))
    chunks = chunk_file(text, max_lines=40, max_chars=100_000, overlap=0)
    assert len(chunks) >= 3
    # 1-indexed, contiguous, inclusive
    assert chunks[0].start_line == 1
    for prev, nxt in zip(chunks, chunks[1:]):
        assert nxt.start_line == prev.end_line + 1
    assert chunks[-1].end_line == 100
    # reconstruct exactly
    lines = text.split("\n")
    for c in chunks:
        assert c.text == "\n".join(lines[c.start_line - 1:c.end_line])


def test_chunk_file_overlap():
    text = "\n".join(f"line {i}" for i in range(100))
    chunks = chunk_file(text, max_lines=40, max_chars=100_000, overlap=15)
    assert len(chunks) >= 3
    # consecutive chunks share `overlap` lines and still cover to the end
    for prev, nxt in zip(chunks, chunks[1:]):
        assert nxt.start_line == prev.end_line + 1 - 15
    assert chunks[0].start_line == 1
    assert chunks[-1].end_line == 100


def test_chunk_file_char_cap_splits():
    big = "x" * 900
    chunks = chunk_file(f"{big}\n{big}\n{big}", max_lines=100, max_chars=1500)
    assert len(chunks) == 3  # each ~900 chars, two would exceed 1500


# -- FileIndex.search ----------------------------------------------------------

async def test_search_ranks_most_relevant_file(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "auth.py").write_text("user login password authentication session")
    (root / "math.py").write_text("matrix vector eigenvalue determinant")
    (root / "readme.txt").write_text("welcome to the project")
    home = tmp_path / "home"
    idx = FileIndex(home, FakeEmbedder())
    hits = await idx.search("user login password", root, "proj", threshold=0.3)
    assert hits
    assert hits[0].path == "auth.py"
    assert all(h.score >= hits[-1].score for h in hits)


async def test_search_incremental_reembeds_only_changed(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "a.txt").write_text("alpha alpha alpha")
    (root / "b.txt").write_text("beta beta beta")
    home = tmp_path / "home"
    emb = FakeEmbedder()
    idx = FileIndex(home, emb)
    await idx.search("alpha", root, "proj", threshold=0.3)
    calls_after_first = len(emb.calls)
    embedded_flat = [t for call in emb.calls for t in call]
    assert "alpha alpha alpha" in embedded_flat
    assert "beta beta beta" in embedded_flat

    # Modify only b.txt: only its new chunk should be embedded.
    (root / "b.txt").write_text("gamma gamma gamma")
    before = len(emb.calls)
    await idx.search("gamma", root, "proj", threshold=0.3)
    new_calls = emb.calls[before:]
    # embed_query for "gamma" + one document batch containing only the changed chunk
    doc_calls = [c for c in new_calls if "gamma gamma gamma" in c]
    assert doc_calls, "changed file should be re-embedded"
    # unchanged file text is never re-embedded on the second pass
    assert all("alpha alpha alpha" not in c for c in new_calls)
    assert len(emb.calls) > calls_after_first


async def test_search_prunes_deleted_file(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "keep.txt").write_text("shared keyword here")
    (root / "gone.txt").write_text("shared keyword gone")
    home = tmp_path / "home"
    idx = FileIndex(home, FakeEmbedder())
    await idx.search("shared keyword", root, "proj", threshold=0.3)
    (root / "gone.txt").unlink()
    hits = await idx.search("shared keyword", root, "proj", threshold=0.3)
    paths = {h.path for h in hits}
    assert "gone.txt" not in paths and "keep.txt" in paths
    data = json.loads(project_file_index_path(home, "proj").read_text())
    assert "gone.txt" not in data["files"]


async def test_search_model_change_rebuilds(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "a.txt").write_text("hello there world")
    home = tmp_path / "home"
    await FileIndex(home, FakeEmbedder(model_id="a")).search(
        "hello", root, "proj", threshold=0.3)
    emb = FakeEmbedder(model_id="b")
    idx = FileIndex(home, emb)
    await idx.search("hello", root, "proj", threshold=0.3)
    data = json.loads(project_file_index_path(home, "proj").read_text())
    assert data["model_id"] == "b"


async def test_search_no_project_returns_empty(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "a.txt").write_text("content")
    idx = FileIndex(tmp_path / "home", FakeEmbedder())
    assert await idx.search("content", root, None) == []


def test_default_threshold_constant():
    assert DEFAULT_THRESHOLD == 0.45


# -- FileIndex.reindex ---------------------------------------------------------

async def test_reindex_reports_monotonic_progress(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    for i in range(5):
        (root / f"f{i}.txt").write_text(f"unique content number {i}")
    home = tmp_path / "home"
    idx = FileIndex(home, FakeEmbedder())
    calls: list[tuple[int, int]] = []
    await idx.reindex(root, "proj", progress=lambda d, t: calls.append((d, t)))
    assert calls, "progress should be reported"
    assert calls[0][0] == 0  # starts at done=0
    total = calls[0][1]
    assert total == 5  # one chunk per small file
    assert calls[-1] == (total, total)  # finishes fully done
    dones = [d for d, _ in calls]
    assert dones == sorted(dones)  # monotonic non-decreasing
    # index persisted
    assert project_file_index_path(home, "proj").is_file()


async def test_reindex_warm_project_embeds_nothing(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "a.txt").write_text("alpha alpha alpha")
    home = tmp_path / "home"
    emb = FakeEmbedder()
    idx = FileIndex(home, emb)
    await idx.reindex(root, "proj")
    before = len(emb.calls)
    calls: list[tuple[int, int]] = []
    await idx.reindex(root, "proj", progress=lambda d, t: calls.append((d, t)))
    # nothing changed: no new document embeds, total is 0
    assert len(emb.calls) == before
    assert calls == [(0, 0)]


async def test_reindex_progress_none_matches_default(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "a.txt").write_text("shared content one")
    (root / "b.txt").write_text("shared content two")
    home = tmp_path / "home"
    idx = FileIndex(home, FakeEmbedder())
    await idx.reindex(root, "proj")  # progress=None
    hits = await idx.search("shared content", root, "proj", threshold=0.3)
    assert {h.path for h in hits} == {"a.txt", "b.txt"}
