import json

import pytest

from forge.engine.memindex import (
    DEFAULT_THRESHOLD, INDEX_FILE, INDEX_SCHEMA_VERSION, MemoryIndex,
    _cosine, chunk_region,
)
from forge.llm.embeddings import FakeEmbedder


# -- chunker -------------------------------------------------------------------

def test_chunker_line_numbers_and_boundaries():
    text = "# Title\n\nfirst paragraph\nsecond line\n\n## Section\nbody\n"
    chunks = chunk_region(text)
    # Standalone "# Title" heading is dropped; the first body block survives.
    assert chunks[0].text == "first paragraph\nsecond line"
    assert [c.text for c in chunks][-1].startswith("## Section")
    # 1-indexed inclusive line ranges preserved for surviving chunks
    assert chunks[0].start_line == 3 and chunks[0].end_line == 4
    last = chunks[-1]
    assert last.start_line == 6 and last.end_line == 7
    # reconstruct: every chunk's text matches its claimed line range
    lines = text.split("\n")
    for c in chunks:
        assert c.text == "\n".join(lines[c.start_line - 1:c.end_line])


def test_chunker_one_chunk_per_block_and_heading_starts_block():
    text = "a\nb\n# H\nc\n\nd\n"
    chunks = chunk_region(text)
    assert [c.text for c in chunks] == ["a\nb", "# H\nc", "d"]


def test_chunker_splits_oversized_runs():
    para = "x" * 500
    text = f"{para}\n\n{para}\n\n{para}\n"
    chunks = chunk_region(text)
    assert len(chunks) == 3  # 500+500 > 800 cap


def test_chunker_empty():
    assert chunk_region("") == []
    assert chunk_region("\n\n\n") == []


def test_chunker_drops_heading_only_chunks():
    # Headings separated from their bodies by blank lines become standalone
    # heading-only blocks and must not be indexed.
    text = "# Decisions\n\n- use postgres\n\n## Machine & Environment\n\nmacos\n"
    chunks = chunk_region(text)
    assert [c.text for c in chunks] == ["- use postgres", "macos"]
    # Line numbers stay correct for the surviving chunks.
    lines = text.split("\n")
    for c in chunks:
        assert c.text == "\n".join(lines[c.start_line - 1:c.end_line])


def test_chunker_keeps_heading_attached_to_body():
    # A heading on the line directly above its body shares the chunk and is kept.
    text = "# Cross-Project Techniques\ngit sparse checkout trick\n"
    chunks = chunk_region(text)
    assert [c.text for c in chunks] == [
        "# Cross-Project Techniques\ngit sparse checkout trick"]


def test_chunker_drops_multi_heading_only_chunk():
    # Multiple consecutive heading lines with no body are still heading-only.
    text = "# Decisions\n## Details\n\nreal content\n"
    chunks = chunk_region(text)
    # "# Decisions" starts a block; "## Details" starts another (both dropped).
    assert [c.text for c in chunks] == ["real content"]


# -- incremental sync ------------------------------------------------------------

@pytest.mark.asyncio
async def test_sync_embeds_only_changed_chunks(tmp_path):
    mem = tmp_path / "memory"
    mem.mkdir(parents=True)
    (mem / "profile.md").write_text("alpha facts here\n\nbeta facts here\n")
    emb = FakeEmbedder()
    idx = MemoryIndex(tmp_path, emb)

    await idx.sync_region("global", "profile")
    assert emb.calls == [["alpha facts here", "beta facts here"]]

    # Edit only the second chunk: only it re-embeds.
    (mem / "profile.md").write_text("alpha facts here\n\ngamma facts here\n")
    await idx.sync_region("global", "profile")
    assert emb.calls[1] == ["gamma facts here"]

    data = json.loads((mem / INDEX_FILE).read_text())
    texts = [c["text"] for c in data["regions"]["profile"]["chunks"]]
    assert texts == ["alpha facts here", "gamma facts here"]


@pytest.mark.asyncio
async def test_sync_unchanged_file_is_noop(tmp_path):
    mem = tmp_path / "memory"
    mem.mkdir(parents=True)
    (mem / "profile.md").write_text("stable\n")
    emb = FakeEmbedder()
    idx = MemoryIndex(tmp_path, emb)
    await idx.sync_region("global", "profile")
    await idx.sync_region("global", "profile")
    assert len(emb.calls) == 1


@pytest.mark.asyncio
async def test_sync_removes_deleted_region(tmp_path):
    mem = tmp_path / "memory"
    mem.mkdir(parents=True)
    (mem / "profile.md").write_text("to be removed\n")
    idx = MemoryIndex(tmp_path, FakeEmbedder())
    await idx.sync_region("global", "profile")
    (mem / "profile.md").unlink()
    await idx.sync_region("global", "profile")
    data = json.loads((mem / INDEX_FILE).read_text())
    assert data["regions"] == {}


@pytest.mark.asyncio
async def test_sync_project_tier_and_invalid_project(tmp_path):
    pdir = tmp_path / "projects" / "p1" / "memory"
    pdir.mkdir(parents=True)
    (pdir / "state.md").write_text("backlog item\n")
    emb = FakeEmbedder()
    idx = MemoryIndex(tmp_path, emb)
    await idx.sync_region("project", "state", project_id="p1")
    assert (pdir / INDEX_FILE).is_file()
    # invalid project id → silently skipped
    await idx.sync_region("project", "state", project_id="../evil")
    assert not (tmp_path / "projects" / "../evil").exists()


# -- search -----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_ranks_and_thresholds(tmp_path):
    mem = tmp_path / "memory"
    mem.mkdir(parents=True)
    (mem / "profile.md").write_text(
        "user runs macos with homebrew\n\ncompletely unrelated quantum entanglement\n")
    idx = MemoryIndex(tmp_path, FakeEmbedder())
    hits = await idx.search("user runs macos with homebrew", None, threshold=0.7)
    assert len(hits) == 1
    assert hits[0].tier == "global" and hits[0].region == "profile"
    assert hits[0].score > 0.99
    assert hits[0].start_line == 1


@pytest.mark.asyncio
async def test_search_lazily_indexes_existing_memory(tmp_path):
    # Pre-existing memory files with no index yet: first search backfills.
    mem = tmp_path / "memory"
    mem.mkdir(parents=True)
    (mem / "techniques.md").write_text("git sparse checkout trick\n")
    pdir = tmp_path / "projects" / "proj" / "memory"
    pdir.mkdir(parents=True)
    (pdir / "procedures.md").write_text("run pytest with uv\n")
    idx = MemoryIndex(tmp_path, FakeEmbedder())
    hits = await idx.search("run pytest with uv", "proj", threshold=0.7)
    assert [(h.tier, h.region) for h in hits] == [("project", "procedures")]
    assert (mem / INDEX_FILE).is_file() and (pdir / INDEX_FILE).is_file()


@pytest.mark.asyncio
async def test_search_top_k(tmp_path):
    mem = tmp_path / "memory"
    mem.mkdir(parents=True)
    (mem / "context.md").write_text("\n\n".join(f"same words {i}" for i in range(8)) + "\n")
    idx = MemoryIndex(tmp_path, FakeEmbedder())
    hits = await idx.search("same words", None, top_k=5, threshold=0.5)
    assert len(hits) == 5
    assert all(h.score >= hits[-1].score for h in hits)


@pytest.mark.asyncio
async def test_search_reconciles_stale_index(tmp_path):
    mem = tmp_path / "memory"
    mem.mkdir(parents=True)
    (mem / "profile.md").write_text("old fact\n")
    idx = MemoryIndex(tmp_path, FakeEmbedder())
    await idx.search("old fact", None, threshold=0.5)
    # file edited outside the dream pass
    (mem / "profile.md").write_text("brand new fact\n")
    hits = await idx.search("brand new fact", None, threshold=0.7)
    assert [h.text for h in hits] == ["brand new fact"]


@pytest.mark.asyncio
async def test_corrupt_index_recovers(tmp_path):
    mem = tmp_path / "memory"
    mem.mkdir(parents=True)
    (mem / "profile.md").write_text("fact\n")
    (mem / INDEX_FILE).write_text("{not json")
    idx = MemoryIndex(tmp_path, FakeEmbedder())
    hits = await idx.search("fact", None, threshold=0.5)
    assert len(hits) == 1


# -- threshold configuration --------------------------------------------------

@pytest.mark.asyncio
async def test_search_uses_default_threshold(tmp_path):
    mem = tmp_path / "memory"
    mem.mkdir(parents=True)
    (mem / "profile.md").write_text(
        "user runs macos with homebrew\n\ncompletely unrelated quantum entanglement\n")
    idx = MemoryIndex(tmp_path, FakeEmbedder())
    assert idx.threshold == DEFAULT_THRESHOLD == 0.45
    # exact match scores ~1.0, unrelated chunk falls below the default threshold
    hits = await idx.search("user runs macos with homebrew", None)
    assert [h.text for h in hits] == ["user runs macos with homebrew"]


@pytest.mark.asyncio
async def test_configurable_threshold_and_explicit_override(tmp_path):
    mem = tmp_path / "memory"
    mem.mkdir(parents=True)
    (mem / "profile.md").write_text("shared word alpha\n\nshared word beta\n")
    # A very high instance threshold suppresses partial matches...
    idx = MemoryIndex(tmp_path, FakeEmbedder(), threshold=0.99)
    assert idx.threshold == 0.99
    assert await idx.search("shared word", None) == []
    # ...but an explicit per-call override still wins.
    hits = await idx.search("shared word", None, threshold=0.1)
    assert len(hits) == 2


# -- index metadata / invalidation --------------------------------------------

@pytest.mark.asyncio
async def test_index_persists_schema_and_model_metadata(tmp_path):
    mem = tmp_path / "memory"
    mem.mkdir(parents=True)
    (mem / "profile.md").write_text("fact\n")
    emb = FakeEmbedder()
    idx = MemoryIndex(tmp_path, emb)
    await idx.sync_region("global", "profile")
    data = json.loads((mem / INDEX_FILE).read_text())
    assert data["schema_version"] == INDEX_SCHEMA_VERSION
    assert data["model_id"] == emb.model_id
    assert data["dimensions"] == emb.dimensions


@pytest.mark.asyncio
async def test_legacy_index_without_metadata_is_rebuilt(tmp_path):
    mem = tmp_path / "memory"
    mem.mkdir(parents=True)
    (mem / "profile.md").write_text("fresh fact\n")
    # Legacy {regions:...} index with a bogus vector and no metadata.
    (mem / INDEX_FILE).write_text(json.dumps({"regions": {
        "profile": {"hash": "stale", "chunks": [
            {"hash": "x", "start_line": 1, "end_line": 1,
             "text": "old fact", "vector": [0.0] * 4}]}}}))
    emb = FakeEmbedder()
    idx = MemoryIndex(tmp_path, emb)
    hits = await idx.search("fresh fact", None, threshold=0.5)
    assert [h.text for h in hits] == ["fresh fact"]
    data = json.loads((mem / INDEX_FILE).read_text())
    assert data["schema_version"] == INDEX_SCHEMA_VERSION
    assert data["model_id"] == emb.model_id


@pytest.mark.asyncio
async def test_schema_bump_rebuilds_and_purges_heading_only_chunks(tmp_path):
    mem = tmp_path / "memory"
    mem.mkdir(parents=True)
    (mem / "decisions.md").write_text("# Decisions\n\n- use postgres\n")
    emb = FakeEmbedder()
    # Stale v2 index that indexed a heading-only "# Decisions" chunk.
    (mem / INDEX_FILE).write_text(json.dumps({
        "schema_version": 2, "model_id": emb.model_id,
        "dimensions": emb.dimensions,
        "regions": {"decisions": {"hash": "stale", "chunks": [
            {"hash": "h", "start_line": 1, "end_line": 1,
             "text": "# Decisions", "vector": [0.0] * emb.dimensions}]}}}))
    idx = MemoryIndex(tmp_path, emb)
    await idx.sync_region("global", "decisions")
    data = json.loads((mem / INDEX_FILE).read_text())
    assert data["schema_version"] == INDEX_SCHEMA_VERSION
    texts = [c["text"] for c in data["regions"]["decisions"]["chunks"]]
    assert texts == ["- use postgres"]


@pytest.mark.asyncio
async def test_model_change_invalidates_index(tmp_path):
    mem = tmp_path / "memory"
    mem.mkdir(parents=True)
    (mem / "profile.md").write_text("fact one\n")
    await MemoryIndex(tmp_path, FakeEmbedder(model_id="a")).sync_region(
        "global", "profile")
    emb = FakeEmbedder(model_id="b")
    idx = MemoryIndex(tmp_path, emb)
    await idx.sync_region("global", "profile")
    data = json.loads((mem / INDEX_FILE).read_text())
    assert data["model_id"] == "b"


@pytest.mark.asyncio
async def test_dimension_change_invalidates_index(tmp_path):
    mem = tmp_path / "memory"
    mem.mkdir(parents=True)
    (mem / "profile.md").write_text("fact two\n")
    await MemoryIndex(tmp_path, FakeEmbedder(dims=32)).sync_region(
        "global", "profile")
    emb = FakeEmbedder(dims=16)
    idx = MemoryIndex(tmp_path, emb)
    await idx.sync_region("global", "profile")
    data = json.loads((mem / INDEX_FILE).read_text())
    assert data["dimensions"] == 16
    chunk = data["regions"]["profile"]["chunks"][0]
    assert len(chunk["vector"]) == 16


# -- query/document parity ----------------------------------------------------

@pytest.mark.asyncio
async def test_query_and_document_embedding_parity(tmp_path):
    emb = FakeEmbedder()
    q = await emb.embed_query("run pytest with uv")
    d = (await emb.embed(["run pytest with uv"]))[0]
    assert q == d  # no query-only instruction prefix


def test_cosine_rejects_unequal_dimensions():
    with pytest.raises(ValueError):
        _cosine([1.0, 0.0, 0.0], [1.0, 0.0])
