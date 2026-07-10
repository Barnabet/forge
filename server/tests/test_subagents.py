import asyncio
import json

from forge.engine.actor import SessionActor, SessionMeta
from forge.engine.bus import EventBus
from forge.engine.events import ToolCallSpec
from forge.engine.scheduler import Scheduler
from forge.llm.base import CompletionResult
from forge.llm.fake import FakeLLM
from forge.store.changesets import ChangesetStore
from forge.store.config import ForgeConfig
from forge.tools.base import ToolContext
from forge.tools.subagents import SpawnAgentsTool

from tests.test_actor import wait_idle


class InspectingLLM:
    def __init__(self):
        self.calls = []
        self.active = 0
        self.peak = 0

    async def complete(self, model, messages, tools, on_text_delta,
                       effort="default", on_tool_start=None):
        self.calls.append((messages, tools))
        self.active += 1
        self.peak = max(self.peak, self.active)
        await asyncio.sleep(0.02)
        self.active -= 1
        task = messages[0]["content"].split("## Delegated task\n", 1)[1]
        return CompletionResult(text=f"report for {task}")

    async def healthy(self):
        return True


def make_tool(tmp_path, llm, max_concurrent=4):
    return SpawnAgentsTool(
        llm=llm, skill_dirs=[], model_fn=lambda: "m", effort_fn=lambda: "low",
        parent_prompt_fn=lambda: "PARENT CONTEXT", max_concurrent=max_concurrent)


async def test_read_subagents_run_concurrently_and_return_ordered_reports(tmp_path):
    llm = InspectingLLM()
    tool = make_tool(tmp_path, llm, max_concurrent=2)
    chunks = []

    result = await tool.run({"tasks": [
        {"task": "inspect alpha"}, {"task": "inspect beta", "mode": "read"},
    ]}, ToolContext(cwd=tmp_path, emit_chunk=chunks.append))

    assert not result.is_error
    assert llm.peak == 2
    assert result.output.index("Subagent 1") < result.output.index("Subagent 2")
    assert "report for inspect alpha" in result.output
    assert any("started" in chunk for chunk in chunks)
    # Read workers cannot mutate, shell out, or recursively delegate.
    tool_names = {spec["function"]["name"] for spec in llm.calls[0][1]}
    assert tool_names == {"read_file", "glob", "grep", "list_dir", "load_skill"}
    assert "spawn_agents" not in tool_names


async def test_write_workers_are_serialized(tmp_path):
    llm = InspectingLLM()
    tool = make_tool(tmp_path, llm)

    result = await tool.run({"tasks": [
        {"task": "change alpha", "mode": "write"},
        {"task": "change beta", "mode": "write"},
    ]}, ToolContext(cwd=tmp_path, changesets=ChangesetStore(tmp_path / "sets")))

    assert not result.is_error
    assert llm.peak == 1
    tool_names = {spec["function"]["name"] for spec in llm.calls[0][1]}
    assert {"bash", "write_file", "edit_file"} <= tool_names


async def test_worker_can_use_tools_then_report(tmp_path):
    (tmp_path / "fact.txt").write_text("durable fact")
    llm = FakeLLM([
        CompletionResult(text="", tool_calls=[ToolCallSpec(
            id="read1", name="read_file",
            arguments=json.dumps({"path": "fact.txt", "offset": 1, "limit": 10}))]),
        CompletionResult(text="Found the durable fact."),
    ])
    tool = make_tool(tmp_path, llm)

    result = await tool.run(
        {"tasks": [{"task": "inspect fact.txt"}]}, ToolContext(cwd=tmp_path))

    assert "Found the durable fact" in result.output
    tool_message = next(message for message in llm.calls[1] if message["role"] == "tool")
    assert "durable fact" in tool_message["content"]


async def test_actor_exposes_spawn_agents_and_parent_receives_report(tmp_path):
    cwd = tmp_path / "work"
    cwd.mkdir()
    llm = FakeLLM([
        CompletionResult(text="", tool_calls=[ToolCallSpec(
            id="spawn1", name="spawn_agents", arguments=json.dumps({
                "tasks": [{"task": "research the code"}]}))]),
        CompletionResult(text="Worker found the answer."),
        CompletionResult(text="Final parent response."),
    ])
    actor = SessionActor(
        meta=SessionMeta(id="s1", cwd=str(cwd), model="m"), home=tmp_path / "home",
        config=ForgeConfig(), llm=llm, bus=EventBus(), scheduler=Scheduler(1),
        system_prompt_fn=lambda _meta: "SYS")

    await actor.post_message("delegate this")
    await wait_idle(actor)

    finished = next(e for e in actor.log.read()
                    if e.type == "tool_call_finished" and e.tool == "spawn_agents")
    assert "Worker found the answer" in finished.output
    assert llm.calls[2][-1]["role"] == "tool"


async def test_guarded_session_allows_read_only_subagents_without_gate(tmp_path):
    cwd = tmp_path / "work"
    cwd.mkdir()
    actor = SessionActor(
        meta=SessionMeta(id="s1", cwd=str(cwd), model="m", autonomy="guarded"),
        home=tmp_path / "home", config=ForgeConfig(), llm=FakeLLM([
            CompletionResult(text="", tool_calls=[ToolCallSpec(
                id="spawn1", name="spawn_agents",
                arguments='{"tasks":[{"task":"inspect"}]}')]),
            CompletionResult(text="inspection complete"),
            CompletionResult(text="done"),
        ]), bus=EventBus(), scheduler=Scheduler(1), system_prompt_fn=lambda _meta: "SYS")

    await actor.post_message("delegate")
    await wait_idle(actor)

    assert not any(e.type == "approval_requested" for e in actor.log.read())
    started = next(e for e in actor.log.read() if e.type == "tool_call_started")
    assert not started.auto_approved


async def test_guarded_session_gates_write_subagent_dispatch(tmp_path):
    cwd = tmp_path / "work"
    cwd.mkdir()
    actor = SessionActor(
        meta=SessionMeta(id="s1", cwd=str(cwd), model="m", autonomy="guarded"),
        home=tmp_path / "home", config=ForgeConfig(), llm=FakeLLM([
            CompletionResult(text="", tool_calls=[ToolCallSpec(
                id="spawn1", name="spawn_agents",
                arguments='{"tasks":[{"task":"edit safely","mode":"write"}]}')]),
        ]), bus=EventBus(), scheduler=Scheduler(1), system_prompt_fn=lambda _meta: "SYS")

    await actor.post_message("delegate")
    for _ in range(100):
        if any(e.type == "approval_requested" for e in actor.log.read()):
            break
        await asyncio.sleep(0.01)
    approval = next(e for e in actor.log.read() if e.type == "approval_requested")
    assert approval.tool == "spawn_agents"
    await actor.resolve_approval("spawn1", "deny")
    await wait_idle(actor)
