import pytest

from forge.engine.bus import EventBus
from forge.engine.fileindex import FileIndex
from forge.engine.manager import SessionManager
from forge.llm.embeddings import FakeEmbedder
from forge.llm.fake import FakeLLM
from forge.store.config import ForgeConfig
from forge.tools.base import ToolContext
from forge.tools.file_search import SearchFilesTool


def seed_project(root):
    (root / "auth.py").write_text(
        "def login(user, password):\n    verify credentials and issue a token\n")
    (root / "billing.py").write_text(
        "def charge(card):\n    process a payment through the gateway\n")


# -- tool behavior ------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_files_returns_ranked_snippets(tmp_path):
    seed_project(tmp_path)
    tool = SearchFilesTool(FileIndex(tmp_path, FakeEmbedder()), "proj")
    r = await tool.run({"query": "verify credentials and issue a token"},
                       ToolContext(cwd=tmp_path))
    assert not r.is_error
    assert "[auth.py:" in r.output
    # The auth file (word overlap with the query) must rank above billing.
    if "[billing.py:" in r.output:
        assert r.output.index("[auth.py:") < r.output.index("[billing.py:")


@pytest.mark.asyncio
async def test_search_files_no_match(tmp_path):
    seed_project(tmp_path)
    tool = SearchFilesTool(FileIndex(tmp_path, FakeEmbedder()), "proj")
    r = await tool.run({"query": "zebra quantum xylophone unrelated"},
                       ToolContext(cwd=tmp_path))
    assert r.output == "No files matched."


@pytest.mark.asyncio
async def test_search_files_none_project_returns_no_match(tmp_path):
    seed_project(tmp_path)
    tool = SearchFilesTool(FileIndex(tmp_path, FakeEmbedder()), None)
    r = await tool.run({"query": "verify credentials"}, ToolContext(cwd=tmp_path))
    assert r.output == "No files matched."


def test_search_files_is_read_only_and_displays_query(tmp_path):
    tool = SearchFilesTool(FileIndex(tmp_path, FakeEmbedder()), "proj")
    assert tool.read_only and not tool.requires_approval({})
    assert tool.display({"query": "where is auth"}) == "where is auth"


# -- registration -------------------------------------------------------------

def test_search_files_absent_without_index(tmp_path):
    manager = SessionManager(tmp_path, ForgeConfig(), FakeLLM([]), EventBus())
    actor = manager.create(cwd=str(tmp_path), project_id="proj")
    assert "search_files" not in actor.tools  # no openrouter key → no index
    assert manager.file_index is None


def test_search_files_absent_without_project(tmp_path):
    manager = SessionManager(tmp_path, ForgeConfig(), FakeLLM([]), EventBus())
    manager.file_index = FileIndex(tmp_path, FakeEmbedder())
    actor = manager.create(cwd=str(tmp_path))  # no project_id
    assert "search_files" not in actor.tools


def test_search_files_registered_with_index_and_project(tmp_path):
    manager = SessionManager(tmp_path, ForgeConfig(), FakeLLM([]), EventBus())
    manager.file_index = FileIndex(tmp_path, FakeEmbedder())
    actor = manager.create(cwd=str(tmp_path), project_id="proj")
    assert "search_files" in actor.tools
    assert actor.tools["search_files"].read_only
