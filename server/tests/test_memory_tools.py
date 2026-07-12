import json

import pytest

from forge.engine.bus import EventBus
from forge.engine.events import MemoryRecalled, RecalledSnippet, ToolCallSpec, UserMessage
from forge.engine.manager import SessionManager
from forge.engine.memindex import INDEX_FILE, MemoryIndex
from forge.engine.projection import to_messages
from forge.llm.base import CompletionResult
from forge.llm.embeddings import FakeEmbedder
from forge.llm.fake import FakeLLM
from forge.store.config import ForgeConfig
from forge.tools.base import ToolContext
from forge.tools.memory_tools import ReadMemoryTool, RememberTool

from tests.test_actor import wait_idle


def seed(home, project_id=None):
    mem = home / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    (mem / "profile.md").write_text("user machine runs macos homebrew\n")
    if project_id:
        pdir = home / "projects" / project_id / "memory"
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "procedures.md").write_text("run tests with uv pytest\nsecond line\n")


# -- remember tool ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_remember_returns_addressed_snippets(tmp_path):
    seed(tmp_path, "proj")
    tool = RememberTool(MemoryIndex(tmp_path, FakeEmbedder()), "proj")
    r = await tool.run({"query": "run tests with uv pytest"}, ToolContext(cwd=tmp_path))
    assert not r.is_error
    assert "[project/procedures:1-2 score=" in r.output
    assert "run tests with uv pytest" in r.output


@pytest.mark.asyncio
async def test_remember_no_match(tmp_path):
    seed(tmp_path)
    tool = RememberTool(MemoryIndex(tmp_path, FakeEmbedder()), None)
    r = await tool.run({"query": "zebra quantum xylophone"}, ToolContext(cwd=tmp_path))
    assert r.output == "No memories matched."


def test_remember_is_read_only_and_displays_query(tmp_path):
    tool = RememberTool(MemoryIndex(tmp_path, FakeEmbedder()), None)
    assert tool.read_only and not tool.requires_approval({})
    assert tool.display({"query": "the thing"}) == "the thing"


# -- read_memory tool --------------------------------------------------------------

@pytest.mark.asyncio
async def test_read_memory_line_numbers_and_offset(tmp_path):
    seed(tmp_path, "proj")
    tool = ReadMemoryTool(tmp_path, "proj")
    r = await tool.run({"tier": "project", "region": "procedures"},
                       ToolContext(cwd=tmp_path))
    assert "     1\trun tests with uv pytest" in r.output
    assert "     2\tsecond line" in r.output
    r2 = await tool.run({"tier": "project", "region": "procedures", "offset": 2},
                        ToolContext(cwd=tmp_path))
    assert "second line" in r2.output and "run tests" not in r2.output


@pytest.mark.asyncio
async def test_read_memory_lists_regions_without_args(tmp_path):
    seed(tmp_path, "proj")
    r = await ReadMemoryTool(tmp_path, "proj").run({}, ToolContext(cwd=tmp_path))
    assert "global/profile — 33 chars" in r.output
    assert "project/procedures" in r.output
    # project-less session: only the global tier is listed
    r2 = await ReadMemoryTool(tmp_path, None).run({}, ToolContext(cwd=tmp_path))
    assert "project/" not in r2.output


@pytest.mark.asyncio
async def test_read_memory_rejects_bad_region_and_tier(tmp_path):
    seed(tmp_path)
    tool = ReadMemoryTool(tmp_path, None)
    r = await tool.run({"tier": "global", "region": "nope"}, ToolContext(cwd=tmp_path))
    assert r.is_error and "Unknown global region" in r.output
    r2 = await tool.run({"tier": "project", "region": "state"}, ToolContext(cwd=tmp_path))
    assert r2.is_error and "Tier not available" in r2.output
    r3 = await tool.run({"tier": "global", "region": "context"}, ToolContext(cwd=tmp_path))
    assert r3.output == "(empty)"


# -- registration -------------------------------------------------------------------

def test_tool_registration_gated_on_index(tmp_path):
    manager = SessionManager(tmp_path, ForgeConfig(), FakeLLM([]), EventBus())
    actor = manager.create(cwd=str(tmp_path))
    assert "read_memory" in actor.tools  # always available
    assert "remember" not in actor.tools  # no openrouter key → no index
    assert manager.memory_index is None


def test_remember_registered_with_index(tmp_path):
    manager = SessionManager(tmp_path, ForgeConfig(), FakeLLM([]), EventBus())
    manager.memory_index = MemoryIndex(tmp_path, FakeEmbedder())
    manager.memory_agent.index = manager.memory_index
    actor = manager.create(cwd=str(tmp_path))
    assert "remember" in actor.tools
    assert actor.tools["remember"].read_only


def test_prompt_advertises_remember_iff_tool_registered(tmp_path):
    # No index: remember tool absent, and the prompt must not mention it.
    plain_mgr = SessionManager(tmp_path, ForgeConfig(), FakeLLM([]), EventBus())
    plain = plain_mgr.create(cwd=str(tmp_path))
    plain_sp = plain.system_prompt_fn(plain.meta)
    assert "remember" not in plain.tools
    assert "`remember`" not in plain_sp
    assert "read_memory" in plain_sp

    # Indexed: remember tool present, and the prompt advertises it.
    idx_mgr = SessionManager(tmp_path, ForgeConfig(), FakeLLM([]), EventBus())
    idx_mgr.memory_index = MemoryIndex(tmp_path, FakeEmbedder())
    idx_mgr.memory_agent.index = idx_mgr.memory_index
    idx = idx_mgr.create(cwd=str(tmp_path))
    idx_sp = idx.system_prompt_fn(idx.meta)
    assert "remember" in idx.tools
    assert "`remember`" in idx_sp
    assert "read_memory" in idx_sp


# -- per-prompt retrieval (durable MemoryRecalled + projection) -----------------------

def make_manager_with_index(home):
    llm = FakeLLM([CompletionResult(text="done", usage_tokens=5)])
    manager = SessionManager(home, ForgeConfig(), llm, EventBus())
    manager.memory_index = MemoryIndex(home, FakeEmbedder())
    manager.memory_agent.index = manager.memory_index
    return manager, llm


@pytest.mark.asyncio
async def test_user_prompt_triggers_recall_event_and_projection(tmp_path):
    seed(tmp_path, "proj")
    manager, llm = make_manager_with_index(tmp_path)
    actor = manager.create(cwd=str(tmp_path), project_id="proj")
    await actor.post_message("run tests with uv pytest second line")
    await wait_idle(actor)

    events = actor.log.read()
    recalls = [e for e in events if e.type == "memory_recalled"]
    assert len(recalls) == 1
    assert recalls[0].seq > 0  # durable
    user_seq = next(e.seq for e in events if e.type == "user_message")
    assert recalls[0].user_seq == user_seq
    assert any("uv pytest" in s.text for s in recalls[0].snippets)

    # The LLM saw the snippets below the user message.
    sent = llm.calls[0]
    user_msg = next(m for m in sent if m["role"] == "user")
    assert "[project/procedures:" in user_msg["content"]
    assert user_msg["content"].index("uv pytest second line") \
        < user_msg["content"].index("[project/procedures:")


@pytest.mark.asyncio
async def test_no_recall_event_without_index(tmp_path):
    seed(tmp_path)
    llm = FakeLLM([CompletionResult(text="done", usage_tokens=5)])
    manager = SessionManager(tmp_path, ForgeConfig(), llm, EventBus())
    actor = manager.create(cwd=str(tmp_path))
    await actor.post_message("anything at all")
    await wait_idle(actor)
    assert not [e for e in actor.log.read() if e.type == "memory_recalled"]
    assert "snippets retrieved for the message" not in llm.calls[0][-1]["content"]


@pytest.mark.asyncio
async def test_recall_error_is_logged_and_run_succeeds(tmp_path, caplog):
    seed(tmp_path, "proj")
    manager, llm = make_manager_with_index(tmp_path)
    actor = manager.create(cwd=str(tmp_path), project_id="proj")

    async def boom(*args, **kwargs):
        raise RuntimeError("index unavailable")

    manager.memory_index.search = boom
    with caplog.at_level("WARNING", logger="forge.engine.actor"):
        await actor.post_message("run tests with uv pytest second line")
        await wait_idle(actor)

    # The run still completes despite the recall failure.
    events = actor.log.read()
    assert [e for e in events if e.type == "run_finished"][-1].reason == "completed"
    # No durable recall event was written (retry semantics preserved).
    assert not [e for e in events if e.type == "memory_recalled"]
    # The failure was logged with session id, user seq, and exception details.
    rec = next(r for r in caplog.records if "memory recall failed" in r.message)
    user_seq = next(e.seq for e in events if e.type == "user_message")
    assert actor.meta.id in rec.getMessage()
    assert str(user_seq) in rec.getMessage()
    assert rec.exc_info is not None
    assert "index unavailable" in caplog.text


@pytest.mark.asyncio
async def test_recall_skipped_when_nothing_matches(tmp_path):
    seed(tmp_path)
    manager, llm = make_manager_with_index(tmp_path)
    actor = manager.create(cwd=str(tmp_path))
    await actor.post_message("zebra quantum xylophone")
    await wait_idle(actor)
    recalls = [e for e in actor.log.read() if e.type == "memory_recalled"]
    assert len(recalls) == 1 and recalls[0].snippets == []
    assert "snippets retrieved for the message" not in llm.calls[0][-1]["content"]


def test_projection_appends_recall_to_multimodal_message():
    user = UserMessage(seq=1, session_id="s", ts=0.0, text="query",
                       images=["data:image/png;base64,x"])
    recall = MemoryRecalled(seq=2, session_id="s", ts=0.0, user_seq=1, snippets=[
        RecalledSnippet(tier="global", region="profile", start_line=1, end_line=1,
                        text="fact", score=0.9)])
    msgs = to_messages([user, recall], "SP")
    content = msgs[1]["content"]
    assert isinstance(content, list)
    assert content[-1]["type"] == "text"
    assert "<recalled-memories>" in content[-1]["text"]
    assert "[global/profile:1-1 score=0.90]" in content[-1]["text"]


# -- dream pass re-embeds written regions ----------------------------------------------

@pytest.mark.asyncio
async def test_dream_write_syncs_index(tmp_path):
    llm = FakeLLM(
        [CompletionResult(text="done", usage_tokens=5)],
        memory_script=[
            CompletionResult(text="", tool_calls=[ToolCallSpec(
                id="c1", name="write_memory", arguments=json.dumps(
                    {"tier": "global", "region": "techniques",
                     "content": "new trick learned"}))]),
            CompletionResult(text="noted"),
        ])
    manager = SessionManager(tmp_path, ForgeConfig(), llm, EventBus())
    embedder = FakeEmbedder()
    manager.memory_index = MemoryIndex(tmp_path, embedder)
    manager.memory_agent.index = manager.memory_index
    actor = manager.create(cwd=str(tmp_path))
    await actor.post_message("do something")
    await wait_idle(actor)
    await actor.memory_task
    assert ["new trick learned"] in embedder.calls
    data = json.loads((tmp_path / "memory" / INDEX_FILE).read_text())
    assert "techniques" in data["regions"]
