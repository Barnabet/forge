import asyncio
import json

from forge.engine.actor import SessionMeta
from forge.engine.bus import EventBus
from forge.engine.events import ToolCallSpec
from forge.engine.manager import SessionManager
from forge.engine.memory import MemoryAgent, read_global_memory, read_project_memory
from forge.engine.sysprompt import build_system_prompt
from forge.llm.base import CompletionResult
from forge.llm.fake import FakeLLM
from forge.store.config import ForgeConfig

from tests.test_actor import wait_idle


def call(name, tier, region, **kw):
    return ToolCallSpec(id=f"c{name}-{region}", name=name,
                        arguments=json.dumps({"tier": tier, "region": region, **kw}))


def write_call(tier, region, content):
    return call("write_memory", tier, region, content=content)


async def test_project_run_writes_regions_and_future_prompt_uses_them(tmp_path):
    home = tmp_path / "home"
    cwd = tmp_path / "work"
    cwd.mkdir()
    llm = FakeLLM(
        [CompletionResult(text="Implemented it with pytest.", usage_tokens=10)],
        memory_script=[
            CompletionResult(text="", tool_calls=[
                write_call("project", "procedures", "- Tests use pytest."),
                write_call("global", "preferences", "- User prefers terse replies."),
            ]),
            CompletionResult(text="updated procedures and preferences"),
        ])
    manager = SessionManager(home, ForgeConfig(), llm, EventBus())
    actor = manager.create(cwd=str(cwd), project_id="project1")

    await actor.post_message("Please implement the feature")
    await wait_idle(actor)
    await actor.memory_task

    assert "Tests use pytest" in read_project_memory(home, "project1")
    assert "### Procedures" in read_project_memory(home, "project1")
    assert "terse replies" in read_global_memory(home)
    # Memory content is never inlined into the prompt; the prompt only
    # documents the recall tools and regions.
    prompt = build_system_prompt(
        SessionMeta(id="next", cwd=str(cwd), model="m", project_id="project1"),
        home, memory_search=True)
    assert "## Memory" in prompt
    memory_section = prompt.split("## Memory\n", 1)[1].split("\n\n", 1)[0]
    assert "remember" in memory_section and "read_memory" in memory_section
    assert "Tests use pytest" not in prompt and "terse replies" not in prompt
    # The dreamer sees the brain manual, the run transcript, and untrusted-data guard.
    dream_prompt = llm.memory_calls[0][0]["content"]
    assert "You are the memory agent" in dream_prompt
    assert "Implemented it with pytest" in dream_prompt
    assert "untrusted session" in dream_prompt


async def test_non_project_session_dreams_into_global_tier_only(tmp_path):
    home = tmp_path / "home"
    cwd = tmp_path / "work"
    cwd.mkdir()
    llm = FakeLLM(
        [CompletionResult(text="done", usage_tokens=10)],
        memory_script=[
            CompletionResult(text="", tool_calls=[
                write_call("global", "profile", "- Uses macOS."),
                write_call("project", "architecture", "- should be rejected"),
            ]),
            CompletionResult(text="noted"),
        ])
    manager = SessionManager(home, ForgeConfig(), llm, EventBus())
    actor = manager.create(cwd=str(cwd))

    await actor.post_message("hello")
    await wait_idle(actor)
    await actor.memory_task

    assert "Uses macOS" in read_global_memory(home)
    assert 'unavailable' in llm.memory_calls[0][0]["content"]  # told up front
    tool_results = [m["content"] for m in llm.memory_calls[1] if m.get("role") == "tool"]
    assert any("unavailable" in r for r in tool_results)
    assert not list((home / "projects").glob("**/*.md")) if (home / "projects").exists() else True


async def test_dreamer_cannot_escape_memory_regions(tmp_path):
    home = tmp_path / "home"
    secret = tmp_path / "secret.txt"
    secret.write_text("do not touch")
    agent = MemoryAgent(home, FakeLLM([], memory_script=[
        CompletionResult(text="", tool_calls=[
            call("write_memory", "project", "../../secret", content="pwn"),
            call("write_memory", "global", "../sessions/x", content="pwn"),
            call("read_memory", "global", "../../secret.txt"),
        ]),
        CompletionResult(text="done"),
    ]))

    wrote = await agent.update("p1", "m", "default", "USER: hi")

    assert not wrote
    assert secret.read_text() == "do not touch"
    files = [p for p in home.rglob("*") if p.is_file()]
    assert files == []


async def test_edit_memory_is_surgical_and_read_returns_content(tmp_path):
    home = tmp_path / "home"
    region = home / "projects" / "p1" / "memory" / "conventions.md"
    region.parent.mkdir(parents=True)
    region.write_text("- Use uv.\n- Use npm.\n")
    agent = MemoryAgent(home, FakeLLM([], memory_script=[
        CompletionResult(text="", tool_calls=[call("read_memory", "project", "conventions")]),
        CompletionResult(text="", tool_calls=[
            call("edit_memory", "project", "conventions",
                 old_string="- Use npm.", new_string="- Use pnpm.")]),
        CompletionResult(text="switched npm to pnpm"),
    ]))
    llm = agent.llm

    wrote = await agent.update("p1", "m", "default", "USER: we use pnpm now")

    assert wrote
    assert region.read_text() == "- Use uv.\n- Use pnpm.\n"
    tool_results = [m["content"] for m in llm.memory_calls[-1] if m.get("role") == "tool"]
    assert tool_results[0] == "- Use uv.\n- Use npm.\n"


async def test_legacy_memory_is_offered_for_migration_and_retired_after_write(tmp_path):
    home = tmp_path / "home"
    legacy_project = home / "projects" / "p1" / "MEMORY.md"
    legacy_project.parent.mkdir(parents=True)
    legacy_project.write_text("- Old fact: tests use pytest.\n")
    legacy_global = home / "memory" / "MEMORY.md"
    legacy_global.parent.mkdir(parents=True)
    legacy_global.write_text("- user prefers pnpm\n")

    # Pre-migration, the old single files still feed the prompt.
    assert "pytest" in read_project_memory(home, "p1")
    assert "pnpm" in read_global_memory(home)

    agent = MemoryAgent(home, FakeLLM([], memory_script=[
        CompletionResult(text="", tool_calls=[
            write_call("project", "procedures", "- Tests use pytest."),
            write_call("global", "preferences", "- Prefers pnpm."),
        ]),
        CompletionResult(text="migrated"),
    ]))

    wrote = await agent.update("p1", "m", "default", "USER: hi")

    assert wrote
    dream_prompt = agent.llm.memory_calls[0][0]["content"]
    assert "Legacy memory" in dream_prompt and "Old fact" in dream_prompt
    assert not legacy_project.exists() and not legacy_global.exists()
    assert "pytest" in read_project_memory(home, "p1")
    assert "pnpm" in read_global_memory(home)


async def test_no_tool_calls_means_no_change_and_legacy_survives(tmp_path):
    home = tmp_path / "home"
    legacy = home / "projects" / "p1" / "MEMORY.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("- Keep me\n")
    agent = MemoryAgent(home, FakeLLM([], memory_script=[
        CompletionResult(text="no change")]))

    changed = await agent.update("p1", "m", "default", "USER: transient request")

    assert not changed
    assert legacy.read_text() == "- Keep me\n"


async def test_dream_turn_cap_stops_runaway_loop(tmp_path):
    home = tmp_path / "home"
    endless = [CompletionResult(text="", tool_calls=[
        call("read_memory", "project", "state")]) for _ in range(50)]
    agent = MemoryAgent(home, FakeLLM([], memory_script=endless))

    changed = await agent.update("p1", "m", "default", "USER: hi")

    assert not changed
    assert len(agent.llm.memory_calls) == 10  # _MAX_TURNS


async def test_region_write_is_capped(tmp_path):
    home = tmp_path / "home"
    agent = MemoryAgent(home, FakeLLM([], memory_script=[
        CompletionResult(text="", tool_calls=[
            write_call("global", "profile", "x" * 20_000)]),
        CompletionResult(text="done"),
    ]))

    await agent.update(None, "m", "default", "USER: hi")

    text = (home / "memory" / "profile.md").read_text()
    assert len(text) <= 8_001  # cap + trailing newline


async def test_concurrent_dreams_serialize_and_merge(tmp_path):
    class CoordinatedLLM:
        def __init__(self):
            self.calls = []

        async def complete(self, model, messages, tools, on_text_delta,
                           effort="default", on_tool_start=None):
            self.calls.append(messages)
            await asyncio.sleep(0.01)
            if len(self.calls) in (1, 3):  # first turn of each dream
                number = 1 if len(self.calls) == 1 else 2
                return CompletionResult(text="", tool_calls=[
                    write_call("project", "decisions", f"- fact {number}")])
            return CompletionResult(text="done")

        async def healthy(self):
            return True

    home = tmp_path / "home"
    llm = CoordinatedLLM()
    agent = MemoryAgent(home, llm)
    await asyncio.gather(
        agent.update("p1", "m", "default", "run one"),
        agent.update("p1", "m", "default", "run two"),
    )

    # The second dream started only after the first finished (lock) and saw its
    # write reflected in the region-size overview.
    assert "decisions=9" in llm.calls[2][0]["content"]
    assert "fact 2" in read_project_memory(home, "p1")


def test_project_memory_is_isolated_and_invalid_id_is_rejected(tmp_path):
    home = tmp_path / "home"
    good = home / "projects" / "good" / "memory" / "architecture.md"
    good.parent.mkdir(parents=True)
    good.write_text("- private fact")

    assert "private fact" in read_project_memory(home, "good")
    assert read_project_memory(home, "other") == ""
    assert read_project_memory(home, "../escape") == ""


async def test_memory_pass_does_not_block_a_fast_follow_up_message(tmp_path):
    class SlowMemoryLLM:
        async def complete(self, model, messages, tools, on_text_delta,
                           effort="default", on_tool_start=None):
            if "memory agent" in messages[0]["content"]:
                await asyncio.sleep(0.2)  # slow dream
                return CompletionResult(text="no change")
            return CompletionResult(text="answer", usage_tokens=10)

        async def healthy(self):
            return True

    cwd = tmp_path / "work"
    cwd.mkdir()
    manager = SessionManager(tmp_path / "home", ForgeConfig(), SlowMemoryLLM(), EventBus())
    actor = manager.create(cwd=str(cwd), project_id="p1")

    await actor.post_message("first")
    await wait_idle(actor)
    # Memory pass is still in flight; a quick follow-up must start a new run.
    assert not actor.memory_task.done()
    await actor.post_message("second")
    await wait_idle(actor)
    await actor.memory_task

    answers = [e for e in actor.log.read() if e.type == "assistant_message"]
    assert len(answers) == 2


async def test_memory_pass_emits_running_then_written_indicator(tmp_path):
    home = tmp_path / "home"
    cwd = tmp_path / "work"
    cwd.mkdir()
    llm = FakeLLM(
        [CompletionResult(text="done", usage_tokens=10)],
        memory_script=[
            CompletionResult(text="", tool_calls=[
                write_call("project", "lessons", "- new fact")]),
            CompletionResult(text="done"),
        ])
    bus = EventBus()
    manager = SessionManager(home, ForgeConfig(), llm, bus)
    actor = manager.create(cwd=str(cwd), project_id="p1")
    q = bus.subscribe()

    await actor.post_message("go")
    await wait_idle(actor)
    await actor.memory_task

    states = [e.state for e in _drain(q) if getattr(e, "type", "") == "memory_update"]
    assert states == ["running", "written"]


async def test_memory_pass_emits_unchanged_when_nothing_written(tmp_path):
    home = tmp_path / "home"
    cwd = tmp_path / "work"
    cwd.mkdir()
    llm = FakeLLM(
        [CompletionResult(text="done", usage_tokens=10)],
        memory_script=[CompletionResult(text="no change")])
    bus = EventBus()
    manager = SessionManager(home, ForgeConfig(), llm, bus)
    actor = manager.create(cwd=str(cwd), project_id="p1")
    q = bus.subscribe()

    await actor.post_message("go")
    await wait_idle(actor)
    await actor.memory_task

    states = [e.state for e in _drain(q) if getattr(e, "type", "") == "memory_update"]
    assert states == ["running", "unchanged"]


def _drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out
