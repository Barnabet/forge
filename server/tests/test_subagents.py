import asyncio
import json

from forge.engine.actor import SessionActor, SessionMeta
from forge.engine.bus import EventBus
from forge.engine.events import ToolCallSpec
from forge.engine.scheduler import Scheduler
from forge.engine.workspace import WorkspaceRegistry
from forge.llm.base import CompletionResult
from forge.llm.fake import FakeLLM
from forge.store.changesets import ChangesetStore
from forge.store.config import ForgeConfig
from forge.tools.base import ToolContext
from forge.tools.subagents import SpawnAgentsTool

from tests.test_actor import wait_idle


_GRADER_MARKER = "You are an impartial evaluator grading the work of an AI subagent."


class InspectingLLM:
    def __init__(self, grade_json=None):
        self.calls = []
        self.grader_calls = []
        self.active = 0
        self.peak = 0
        # JSON string the grader call returns; defaults to a valid grade.
        self.grade_json = grade_json or json.dumps({
            "work_quality": 80, "information_delivery": 75,
            "efficiency": 70, "overall": 78, "rationale": "ok",
            "strengths": ["clear"], "issues": []})

    async def complete(self, model, messages, tools, on_text_delta,
                       effort="default", on_tool_start=None):
        if messages and _GRADER_MARKER in str(messages[0].get("content", "")):
            # Grader calls run inside the worker's slot; count them separately so
            # they don't perturb the worker concurrency peak assertions.
            self.grader_calls.append((model, messages, tools))
            return CompletionResult(text=self.grade_json)
        self.calls.append((messages, tools))
        self.active += 1
        self.peak = max(self.peak, self.active)
        await asyncio.sleep(0.02)
        self.active -= 1
        assert messages[0]["role"] == "system" and messages[1]["role"] == "user"
        task = messages[1]["content"].split("## Delegated task\n", 1)[1]
        return CompletionResult(text=f"report for {task}")

    async def healthy(self):
        return True


def make_tool(tmp_path, llm, max_concurrent=4, max_turns=25, model="m", skill_dirs=None):
    return SpawnAgentsTool(
        llm=llm, skill_dirs=skill_dirs or [], model_fn=lambda: model,
        effort_fn=lambda: "low", parent_prompt_fn=lambda: "PARENT CONTEXT",
        max_concurrent=max_concurrent, max_turns=max_turns)


def _make_skill(root, name, body):
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\n{body}\n")
    return root


async def test_worker_prompt_mirror_is_claude_only(tmp_path):
    # Claude models get the <context> mirror (CLIProxyAPI strips the system
    # message); non-Claude models keep the system message and skip it.
    claude_llm = InspectingLLM()
    await make_tool(tmp_path, claude_llm, model="claude-opus-4-8").run(
        {"tasks": [{"task": "inspect alpha"}]}, ToolContext(cwd=tmp_path))
    assert "<context>" in claude_llm.calls[0][0][1]["content"]

    other_llm = InspectingLLM()
    await make_tool(tmp_path, other_llm, model="gpt-5.2").run(
        {"tasks": [{"task": "inspect alpha"}]}, ToolContext(cwd=tmp_path))
    assert other_llm.calls[0][0][1]["content"] == "## Delegated task\ninspect alpha"


async def test_preloaded_skills_injected_into_worker_context(tmp_path):
    skills_root = _make_skill(tmp_path / "skills", "deploy", "Run the deploy dance.")
    for model in ("claude-opus-4-8", "gpt-5.2"):
        llm = InspectingLLM()
        tool = make_tool(tmp_path, llm, model=model, skill_dirs=[skills_root])
        await tool.run({"tasks": [{"task": "ship it", "skills": ["deploy"]}]},
                       ToolContext(cwd=tmp_path))
        content = llm.calls[0][0][1]["content"]
        assert "## Preloaded skills" in content
        assert "### Skill: deploy" in content
        assert "Run the deploy dance." in content


async def test_unknown_preloaded_skill_errors_without_launching(tmp_path):
    llm = InspectingLLM()
    tool = make_tool(tmp_path, llm, skill_dirs=[tmp_path / "skills"])
    result = await tool.run({"tasks": [{"task": "ship it", "skills": ["nope"]}]},
                            ToolContext(cwd=tmp_path))
    assert result.is_error and "unknown skill" in result.output
    assert llm.calls == []


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


async def test_write_workers_run_concurrently(tmp_path):
    llm = InspectingLLM()
    tool = make_tool(tmp_path, llm)

    result = await tool.run({"tasks": [
        {"task": "change alpha", "mode": "write"},
        {"task": "change beta", "mode": "write"},
    ]}, ToolContext(cwd=tmp_path, changesets=ChangesetStore(tmp_path / "sets")))

    assert not result.is_error
    assert llm.peak == 2
    tool_names = {spec["function"]["name"] for spec in llm.calls[0][1]}
    assert {"bash", "write_file", "edit_file"} <= tool_names


async def test_cross_session_write_workers_overlap_and_keep_provenance(tmp_path):
    class BarrierWriteLLM:
        def __init__(self):
            self.ready = asyncio.Event()
            self.active = 0
            self.peak = 0
            self.arrived = 0

        async def complete(self, model, messages, tools, on_text_delta,
                           effort="default", on_tool_start=None):
            if messages and _GRADER_MARKER in str(messages[0].get("content", "")):
                return CompletionResult(text=json.dumps({
                    "work_quality": 80, "information_delivery": 80,
                    "efficiency": 80, "overall": 80, "rationale": "ok",
                    "strengths": [], "issues": []}))
            self.active += 1
            self.peak = max(self.peak, self.active)
            try:
                if len(messages) == 2:
                    self.arrived += 1
                    if self.arrived == 2:
                        self.ready.set()
                    await asyncio.wait_for(self.ready.wait(), timeout=1)
                    task = messages[1]["content"].split("## Delegated task\n", 1)[1]
                    name = "alpha.txt" if "alpha" in task else "beta.txt"
                    return CompletionResult(text="", tool_calls=[ToolCallSpec(
                        id=f"write-{name}", name="write_file",
                        arguments=json.dumps({"path": name, "content": task}))])
                return CompletionResult(text="done")
            finally:
                self.active -= 1

        async def healthy(self):
            return True

    llm = BarrierWriteLLM()
    ws = WorkspaceRegistry(tmp_path / "home").get(tmp_path)
    states_a, states_b = [], []
    ctx_a = ToolContext(
        cwd=tmp_path, changesets=ChangesetStore(tmp_path / "sets-a"),
        session_id="session-a", call_id="call-a", shared_workspace=ws,
        emit_subagent_state=lambda **kw: states_a.append(kw))
    ctx_b = ToolContext(
        cwd=tmp_path, changesets=ChangesetStore(tmp_path / "sets-b"),
        session_id="session-b", call_id="call-b", shared_workspace=ws,
        emit_subagent_state=lambda **kw: states_b.append(kw))

    result_a, result_b = await asyncio.gather(
        make_tool(tmp_path, llm).run(
            {"tasks": [{"task": "change alpha", "mode": "write"}]}, ctx_a),
        make_tool(tmp_path, llm).run(
            {"tasks": [{"task": "change beta", "mode": "write"}]}, ctx_b))

    assert not result_a.is_error and not result_b.is_error
    assert llm.peak == 2
    assert (tmp_path / "alpha.txt").read_text() == "change alpha"
    assert (tmp_path / "beta.txt").read_text() == "change beta"
    activities = [rec for rec in ws.activity.read() if rec.origin == "subagent"]
    assert {(rec.session_id, rec.call_id) for rec in activities} == {
        ("session-a", "call-a"), ("session-b", "call-b")}
    assert all([e["state"] for e in states][:2] == ["queued", "running"]
               for states in (states_a, states_b))


async def test_sibling_workers_keep_independent_stale_read_baselines(tmp_path):
    (tmp_path / "shared.txt").write_text("original")
    ws = WorkspaceRegistry(tmp_path / "home").get(tmp_path)
    parent_ctx = ToolContext(
        cwd=tmp_path, changesets=ChangesetStore(tmp_path / "sets"),
        session_id="session-a", call_id="call-a", shared_workspace=ws)
    tool = make_tool(tmp_path, InspectingLLM())
    tools = tool._tools("write")

    # Both workers read the same original content before either writes.
    for worker in (1, 2):
        output = await tool._execute_worker_call(
            ToolCallSpec(id=f"read-{worker}", name="read_file",
                         arguments=json.dumps({"path": "shared.txt"})),
            tools, parent_ctx, worker_index=worker)
        assert "original" in output

    first = await tool._execute_worker_call(
        ToolCallSpec(id="write-1", name="write_file", arguments=json.dumps({
            "path": "shared.txt", "content": "worker one"})),
        tools, parent_ctx, worker_index=1)
    second = await tool._execute_worker_call(
        ToolCallSpec(id="write-2", name="write_file", arguments=json.dumps({
            "path": "shared.txt", "content": "worker two"})),
        tools, parent_ctx, worker_index=2)

    assert first.startswith("Wrote")
    assert "changed on disk since this session last read it" in second
    assert (tmp_path / "shared.txt").read_text() == "worker one"
    activity = ws.latest_activity_for(tmp_path / "shared.txt")
    assert activity is not None
    assert activity.action == "subagent worker 1: write_file"


async def test_write_workers_serialize_only_mutating_calls(tmp_path):
    class BashLLM:
        def __init__(self):
            self.first_turns = 0
            self.ready = asyncio.Event()

        async def complete(self, model, messages, tools, on_text_delta,
                           effort="default", on_tool_start=None):
            if messages and _GRADER_MARKER in str(messages[0].get("content", "")):
                return CompletionResult(text=json.dumps({
                    "work_quality": 80, "information_delivery": 80,
                    "efficiency": 80, "overall": 80, "rationale": "ok",
                    "strengths": [], "issues": []}))
            if len(messages) == 2:
                self.first_turns += 1
                if self.first_turns == 2:
                    self.ready.set()
                await asyncio.wait_for(self.ready.wait(), timeout=1)
                task = messages[1]["content"].split("## Delegated task\n", 1)[1]
                name = "alpha.txt" if "alpha" in task else "beta.txt"
                return CompletionResult(text="", tool_calls=[ToolCallSpec(
                    id=f"bash-{name}", name="bash",
                    arguments=json.dumps({
                        "command": f"sleep 0.05; printf done > {name}"}))])
            return CompletionResult(text="done")

        async def healthy(self):
            return True

    states = []
    ws = WorkspaceRegistry(tmp_path / "home").get(tmp_path)
    result = await make_tool(tmp_path, BashLLM()).run({"tasks": [
        {"task": "change alpha", "mode": "write"},
        {"task": "change beta", "mode": "write"},
    ]}, ToolContext(
        cwd=tmp_path, changesets=ChangesetStore(tmp_path / "sets"),
        session_id="session-a", call_id="call-a", shared_workspace=ws,
        emit_subagent_state=lambda **kw: states.append(kw)))

    assert not result.is_error
    assert (tmp_path / "alpha.txt").read_text() == "done"
    assert (tmp_path / "beta.txt").read_text() == "done"
    # Both workers started before contention. Only the loser of the actual bash
    # mutation waits, then returns to running after it acquires the lock.
    assert [e["state"] for e in states if e["worker"] == 1][:2] == [
        "queued", "running"]
    assert [e["state"] for e in states if e["worker"] == 2][:2] == [
        "queued", "running"]
    blocked = [e["worker"] for e in states if e["state"] == "blocked"]
    assert len(blocked) == 1
    loser_states = [e["state"] for e in states if e["worker"] == blocked[0]]
    assert loser_states == ["queued", "running", "blocked", "running", "done"]
    activities = [rec for rec in ws.activity.read() if rec.origin == "subagent"]
    assert {tuple(rec.paths) for rec in activities} == {
        (str(tmp_path / "alpha.txt"),), (str(tmp_path / "beta.txt"),)}


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


async def test_worker_lifecycle_events_emitted(tmp_path):
    (tmp_path / "fact.txt").write_text("durable fact")
    llm = FakeLLM([
        CompletionResult(text="", tool_calls=[ToolCallSpec(
            id="read1", name="read_file",
            arguments=json.dumps({"path": "fact.txt", "offset": 1, "limit": 10}))]),
        CompletionResult(text="Found the durable fact."),
    ])
    tool = make_tool(tmp_path, llm)
    states = []
    activities = []

    await tool.run(
        {"tasks": [{"task": "inspect fact.txt"}]},
        ToolContext(cwd=tmp_path,
                    emit_subagent_state=lambda **kw: states.append(kw),
                    emit_event=lambda **kw: activities.append(kw)))

    seq = [e["state"] for e in states]
    assert seq[0] == "queued" and seq[-1] == "done"
    assert "running" in seq
    assert all(e["worker"] == 1 and e["task"] == "inspect fact.txt"
               and e["mode"] == "read" for e in states)
    # Durable states never carry activity lines.
    assert all("activity" not in e for e in states)
    assert "Found the durable fact" in states[-1]["report"]
    # Activity lines remain on the ephemeral channel only.
    activity = next(e for e in activities if e.get("activity"))
    assert activity["activity"].startswith("read_file · ")
    assert activity["state"] == "running"


async def test_activity_line_shows_paths_relative_to_cwd(tmp_path):
    (tmp_path / "fact.txt").write_text("durable fact")
    abs_path = str(tmp_path / "fact.txt")
    llm = FakeLLM([
        CompletionResult(text="", tool_calls=[ToolCallSpec(
            id="read1", name="read_file",
            arguments=json.dumps({"path": abs_path, "offset": 1, "limit": 10}))]),
        CompletionResult(text="Found the durable fact."),
    ])
    tool = make_tool(tmp_path, llm)
    activities = []

    await tool.run(
        {"tasks": [{"task": "inspect fact.txt"}]},
        ToolContext(cwd=tmp_path, emit_event=lambda **kw: activities.append(kw)))

    activity = next(e for e in activities if e.get("activity"))
    # The absolute cwd prefix is stripped, mirroring main-agent tool lines.
    assert activity["activity"] == "read_file · fact.txt"


async def test_worker_warned_when_approaching_turn_limit(tmp_path):
    (tmp_path / "fact.txt").write_text("durable fact")
    read_call = CompletionResult(text="", tool_calls=[ToolCallSpec(
        id="read1", name="read_file",
        arguments=json.dumps({"path": "fact.txt", "offset": 1, "limit": 10}))])
    llm = FakeLLM([read_call] * 5)  # never returns a report -> hits the limit
    tool = make_tool(tmp_path, llm, max_turns=5)

    result = await tool.run(
        {"tasks": [{"task": "loop forever"}]}, ToolContext(cwd=tmp_path))

    assert "reaching the 5-turn limit" in result.output
    # Countdown warnings appear after turns 2, 3 (remaining 3, 2) only; the
    # final turn's 1-remaining slot is covered by the final-turn directive.
    warnings = [m["content"] for m in llm.calls[-1]
                if m["role"] == "user" and "turn(s) left" in str(m.get("content", ""))]
    assert len(warnings) == 2
    assert "3 turn(s) left" in warnings[0]
    assert "2 turn(s) left" in warnings[1]
    # No redundant adjacent 1-turn warning.
    assert not any("1 turn(s) left" in w for w in warnings)


_FINAL_DIRECTIVE = "This is your final turn."


class CapturingLLM:
    """Scripted LLM that snapshots the messages list on each worker call, so
    per-call state can be inspected (FakeLLM stores a live reference)."""

    def __init__(self, script):
        self.script = list(script)
        self.snapshots: list[list[dict]] = []

    async def complete(self, model, messages, tools, on_text_delta,
                       effort="default", on_tool_start=None):
        if messages and _GRADER_MARKER in str(messages[0].get("content", "")):
            return CompletionResult(text=json.dumps({
                "work_quality": 80, "information_delivery": 75,
                "efficiency": 70, "overall": 78}))
        self.snapshots.append([dict(m) for m in messages])
        item = self.script.pop(0)
        if item.text:
            await on_text_delta(item.text)
        return item

    async def healthy(self):
        return True


async def test_final_turn_directive_present_before_final_call_only(tmp_path):
    (tmp_path / "fact.txt").write_text("durable fact")
    read_call = CompletionResult(text="", tool_calls=[ToolCallSpec(
        id="read1", name="read_file",
        arguments=json.dumps({"path": "fact.txt", "offset": 1, "limit": 10}))])
    llm = CapturingLLM([read_call] * 3)  # never returns a report -> hits the limit
    tool = make_tool(tmp_path, llm, max_turns=3)

    await tool.run({"tasks": [{"task": "loop"}]}, ToolContext(cwd=tmp_path))

    # The directive is absent from the messages of the first two (non-final)
    # calls but present in the final call's messages, as the last message.
    for snap in llm.snapshots[:-1]:
        assert not any(_FINAL_DIRECTIVE in str(m.get("content", "")) for m in snap)
    final = llm.snapshots[-1]
    assert final[-1]["role"] == "user"
    assert _FINAL_DIRECTIVE in str(final[-1]["content"])


async def test_final_turn_directive_present_when_max_turns_is_one(tmp_path):
    llm = CapturingLLM([CompletionResult(text="only report")])
    tool = make_tool(tmp_path, llm, max_turns=1)

    result = await tool.run({"tasks": [{"task": "one shot"}]}, ToolContext(cwd=tmp_path))

    assert "only report" in result.output
    final = llm.snapshots[0]
    assert final[-1]["role"] == "user"
    assert _FINAL_DIRECTIVE in str(final[-1]["content"])


async def test_worker_can_return_report_on_final_turn(tmp_path):
    (tmp_path / "fact.txt").write_text("durable fact")
    llm = CapturingLLM([
        CompletionResult(text="", tool_calls=[ToolCallSpec(
            id="read1", name="read_file",
            arguments=json.dumps({"path": "fact.txt", "offset": 1, "limit": 10}))]),
        CompletionResult(text="Final report on the final turn."),
    ])
    tool = make_tool(tmp_path, llm, max_turns=2)

    result = await tool.run({"tasks": [{"task": "inspect"}]}, ToolContext(cwd=tmp_path))

    assert "Final report on the final turn." in result.output
    # The final call saw the directive as its last message, and ordering stays
    # valid: every assistant tool_calls message is answered by tool results.
    final = llm.snapshots[-1]
    assert final[-1]["role"] == "user"
    assert _FINAL_DIRECTIVE in str(final[-1]["content"])
    roles = [m["role"] for m in final]
    for i, m in enumerate(final):
        if m.get("tool_calls"):
            assert roles[i + 1] == "tool"
    # No duplicate 1-turn warning alongside the directive.
    assert not any("1 turn(s) left" in str(m.get("content", "")) for m in final)


async def test_worker_failure_emits_error_event(tmp_path):
    class ExplodingLLM:
        async def complete(self, *a, **kw):
            raise RuntimeError("boom")

        async def healthy(self):
            return True

    tool = make_tool(tmp_path, ExplodingLLM())
    states = []

    result = await tool.run(
        {"tasks": [{"task": "doomed"}]},
        ToolContext(cwd=tmp_path, emit_subagent_state=lambda **kw: states.append(kw)))

    assert result.is_error
    assert states[-1]["state"] == "error"
    assert "boom" in states[-1]["report"]


async def test_actor_publishes_durable_subagent_states_on_bus(tmp_path):
    cwd = tmp_path / "work"
    cwd.mkdir()
    # The worker's first call emits an ephemeral activity update.
    (cwd / "fact.txt").write_text("durable fact")
    llm = FakeLLM([
        CompletionResult(text="", tool_calls=[ToolCallSpec(
            id="spawn1", name="spawn_agents", arguments=json.dumps({
                "tasks": [{"task": "research the code"}]}))]),
        CompletionResult(text="", tool_calls=[ToolCallSpec(
            id="read1", name="read_file",
            arguments=json.dumps({"path": "fact.txt", "offset": 1, "limit": 10}))]),
        CompletionResult(text="Worker found the answer."),
        CompletionResult(text="Final parent response."),
    ])
    bus = EventBus()
    queue = bus.subscribe()
    actor = SessionActor(
        meta=SessionMeta(id="s1", cwd=str(cwd), model="m"), home=tmp_path / "home",
        config=ForgeConfig(), llm=llm, bus=bus, scheduler=Scheduler(1),
        system_prompt_fn=lambda _meta: "SYS")

    await actor.post_message("delegate this")
    await wait_idle(actor)

    seen = []
    while not queue.empty():
        seen.append(queue.get_nowait())

    # Durable states arrive live on the bus with nonzero seq.
    states = [e for e in seen if getattr(e, "type", "") == "subagent_state"]
    assert [s.state for s in states] == ["queued", "running", "done"]
    assert all(s.call_id == "spawn1" and s.seq > 0 for s in states)
    assert "Worker found the answer" in states[-1].report

    # Ephemeral activity updates stay on the bus but are never persisted (seq=0).
    updates = [e for e in seen if getattr(e, "type", "") == "subagent_update"]
    assert updates and all(u.seq == 0 for u in updates)
    assert any(u.activity.startswith("read_file · ") for u in updates)

    # Replay: durable states persist in the log; ephemeral updates do not.
    logged = actor.log.read()
    logged_states = [e for e in logged if e.type == "subagent_state"]
    assert [s.state for s in logged_states] == ["queued", "running", "done"]
    assert "Worker found the answer" in logged_states[-1].report
    assert all(e.type != "subagent_update" for e in logged)


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


# ---- Task 2: subagent grading ------------------------------------------------

from forge.tools.subagent_grader import (  # noqa: E402
    GRADER_MODEL, WorkerRun, build_grader_messages, parse_grade,
)


def _grade_ctx(tmp_path, records, **extra):
    async def persist(rec):
        records.append(rec)
    return ToolContext(cwd=tmp_path, persist_subagent_grade=persist,
                       parent_context="PARENT SNAPSHOT", orchestrator_model="parent-model",
                       call_id="spawn9", **extra)


async def test_grader_uses_exact_model_no_tools_and_user_role(tmp_path):
    (tmp_path / "fact.txt").write_text("durable fact")
    llm = InspectingLLM()
    tool = make_tool(tmp_path, llm)
    records = []
    await tool.run({"tasks": [{"task": "inspect alpha"}]},
                   _grade_ctx(tmp_path, records))
    assert len(llm.grader_calls) == 1
    model, messages, tools = llm.grader_calls[0]
    assert model == GRADER_MODEL == "claude-opus-4-8"
    assert tools == []
    assert len(messages) == 1 and messages[0]["role"] == "user"
    assert "## Rubric" in messages[0]["content"]


async def test_grade_records_distinct_model_provenance(tmp_path):
    llm = InspectingLLM()
    tool = make_tool(tmp_path, llm, model="worker-model")
    records = []
    await tool.run({"tasks": [{"task": "inspect alpha"}]},
                   _grade_ctx(tmp_path, records))
    rec = records[0]
    assert rec.orchestrator_model == "parent-model"
    assert rec.orchestrator_model_inferred is False
    assert rec.subagent_model == "worker-model"
    assert rec.grader_model == GRADER_MODEL
    assert len({rec.orchestrator_model, rec.subagent_model, rec.grader_model}) == 3


async def test_grader_receives_parent_context_snapshot(tmp_path):
    llm = InspectingLLM()
    tool = make_tool(tmp_path, llm)
    records = []
    await tool.run({"tasks": [{"task": "inspect alpha"}]},
                   _grade_ctx(tmp_path, records))
    assert "PARENT SNAPSHOT" in llm.grader_calls[0][1][0]["content"]
    assert records[0].parent_context == "PARENT SNAPSHOT"


async def test_full_worker_messages_persisted_not_excerpt(tmp_path):
    (tmp_path / "fact.txt").write_text("durable fact")
    llm = FakeLLM([
        CompletionResult(text="", tool_calls=[ToolCallSpec(
            id="read1", name="read_file",
            arguments=json.dumps({"path": "fact.txt", "offset": 1, "limit": 10}))]),
        CompletionResult(text="Found it."),
    ], grader_script=[CompletionResult(text=json.dumps({
        "work_quality": 90, "information_delivery": 90, "efficiency": 90,
        "overall": 90}))])
    tool = make_tool(tmp_path, llm)
    records = []
    await tool.run({"tasks": [{"task": "inspect fact"}]},
                   _grade_ctx(tmp_path, records))
    msgs = records[0].worker_messages
    # Tool call args and tool result output are present verbatim.
    assert any(m.get("tool_calls") for m in msgs)
    assert any(m["role"] == "tool" and "durable fact" in m["content"] for m in msgs)
    assert records[0].status == "success" and records[0].grade.overall == 90
    assert records[0].tool_call_count == 1 and records[0].turn_count == 2


async def test_one_success_grade_per_report(tmp_path):
    llm = InspectingLLM()
    tool = make_tool(tmp_path, llm)
    records = []
    await tool.run({"tasks": [{"task": "a"}, {"task": "b"}]},
                   _grade_ctx(tmp_path, records))
    assert len(records) == 2
    assert all(r.status == "success" for r in records)


async def test_worker_crash_persists_error_record_no_score(tmp_path):
    class ExplodingLLM:
        async def complete(self, model, *a, **kw):
            raise RuntimeError("boom")

        async def healthy(self):
            return True

    tool = make_tool(tmp_path, ExplodingLLM())
    records = []
    await tool.run({"tasks": [{"task": "doomed"}]}, _grade_ctx(tmp_path, records))
    assert len(records) == 1
    assert records[0].status == "error" and records[0].grade is None
    assert "boom" in records[0].error


async def test_worker_crash_midrun_retains_partial_transcript(tmp_path):
    # A worker that dies after doing real work must persist an error record that
    # keeps the partial transcript/metadata (never a fabricated score).
    (tmp_path / "fact.txt").write_text("durable fact")
    llm = FakeLLM([
        CompletionResult(text="", tool_calls=[ToolCallSpec(
            id="read1", name="read_file",
            arguments=json.dumps({"path": "fact.txt", "offset": 1, "limit": 10}))]),
        RuntimeError("died mid-run"),
    ])
    tool = make_tool(tmp_path, llm)
    records = []
    result = await tool.run({"tasks": [{"task": "inspect fact"}]},
                            _grade_ctx(tmp_path, records))
    assert result.is_error
    rec = records[0]
    assert rec.status == "error" and rec.grade is None
    assert "died mid-run" in rec.error
    # Partial transcript and metadata survived the crash.
    assert rec.tool_call_count == 1
    assert any(m["role"] == "tool" and "durable fact" in m["content"]
               for m in rec.worker_messages)
    assert rec.subagent_model == "m"
    # No grader call was made for a crashed worker.
    assert llm.grader_calls == []


async def test_grader_failure_is_non_fatal(tmp_path):
    llm = FakeLLM([CompletionResult(text="report ok")],
                  grader_script=[RuntimeError("grader down")])
    tool = make_tool(tmp_path, llm)
    records = []
    result = await tool.run({"tasks": [{"task": "x"}]}, _grade_ctx(tmp_path, records))
    assert not result.is_error and "report ok" in result.output
    assert records[0].status == "error" and "grader down" in records[0].error


def test_parse_grade_accepts_fenced_json():
    raw = "```json\n{\"work_quality\":10,\"information_delivery\":20," \
          "\"efficiency\":30,\"overall\":25}\n```"
    grade = parse_grade(raw)
    assert grade.work_quality == 10 and grade.overall == 25


def test_parse_grade_rejects_out_of_range():
    import pytest
    with pytest.raises(Exception):
        parse_grade(json.dumps({"work_quality": 200, "information_delivery": 1,
                                "efficiency": 1, "overall": 1}))


def test_grader_prompt_marks_data_untrusted():
    run = WorkerRun(final_report="r", messages=[], model="m")
    msgs = build_grader_messages("t", "read", run, "ctx", 25)
    body = msgs[0]["content"]
    assert "UNTRUSTED DATA" in body and "<worker_transcript>" in body


async def test_concurrent_grading_bounded_and_persisted(tmp_path):
    llm = InspectingLLM()
    tool = make_tool(tmp_path, llm, max_concurrent=2)
    records = []
    await tool.run({"tasks": [{"task": "a"}, {"task": "b"}, {"task": "c"}]},
                   _grade_ctx(tmp_path, records))
    assert llm.peak <= 2
    assert len(records) == 3
