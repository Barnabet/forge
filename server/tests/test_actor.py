import asyncio

from forge.engine.actor import SessionActor, SessionMeta
from forge.engine.bus import EventBus
from forge.engine.events import AssistantMessage, ToolCallSpec
from forge.engine.scheduler import Scheduler
from forge.llm.base import CompletionResult, LLMError
from forge.llm.fake import FakeLLM
from forge.store.config import ForgeConfig
from forge.tools.base import ToolResult
from forge.tools.subagents import SpawnAgentsTool


def make_actor(tmp_path, script, autonomy="yolo", delay=0.0):
    meta = SessionMeta(id="s1", cwd=str(tmp_path / "ws"), model="m", autonomy=autonomy)
    (tmp_path / "ws").mkdir(exist_ok=True)
    llm = FakeLLM(script, delay=delay)
    actor = SessionActor(
        meta=meta, home=tmp_path / "home", config=ForgeConfig(),
        llm=llm, bus=EventBus(), scheduler=Scheduler(3),
        system_prompt_fn=lambda m: "SYS")
    return actor, llm


def types(actor):
    return [e.type for e in actor.log.read()]


async def wait_idle(actor):
    await asyncio.wait_for(actor.run_task, timeout=5)


async def test_text_only_run(tmp_path):
    actor, llm = make_actor(tmp_path, [CompletionResult(text="hi!", usage_tokens=10)])
    await actor.post_message("hello")
    await wait_idle(actor)
    assert types(actor) == [
        "user_message", "session_renamed", "message_checkpointed",
        "status_changed",  # running
        "assistant_message", "run_finished", "status_changed"]  # idle
    assert actor.meta.name == "hello" and actor.meta.status == "idle"
    assert llm.calls[0][0]["content"] == "SYS"


async def test_spawn_uses_completion_model_snapshot_after_live_model_change(tmp_path):
    class SwitchingLLM:
        def __init__(self):
            self.actor = None
            self.models = []
            self.calls = 0

        async def complete(self, model, messages, tools, on_text_delta,
                           effort="default", on_tool_start=None):
            self.models.append(model)
            self.calls += 1
            if self.calls == 1:
                self.actor.meta.model = "new-parent-model"
                return CompletionResult(text="", tool_calls=[ToolCallSpec(
                    id="spawn1", name="spawn_agents", arguments='{"tasks":[{"task":"x"}]}')])
            return CompletionResult(text="done")

        async def healthy(self):
            return True

    class CaptureSpawn(SpawnAgentsTool):
        def __init__(self):
            self.seen_model = None

        async def run(self, args, ctx):
            self.seen_model = ctx.orchestrator_model
            return ToolResult(output="captured")

    llm = SwitchingLLM()
    (tmp_path / "ws").mkdir(exist_ok=True)
    actor = SessionActor(
        meta=SessionMeta(id="s1", cwd=str(tmp_path / "ws"), model="old-parent-model",
                         autonomy="yolo"),
        home=tmp_path / "home", config=ForgeConfig(), llm=llm,
        bus=EventBus(), scheduler=Scheduler(1), system_prompt_fn=lambda _m: "SYS")
    llm.actor = actor
    capture = CaptureSpawn()
    actor.tools["spawn_agents"] = capture

    await actor.post_message("delegate")
    await wait_idle(actor)

    assert llm.models == ["old-parent-model", "new-parent-model"]
    assert capture.seen_model == "old-parent-model"


async def test_tool_run_yolo_auto_approves(tmp_path):
    actor, llm = make_actor(tmp_path, [
        CompletionResult(text="", tool_calls=[
            ToolCallSpec(id="c1", name="bash", arguments='{"command": "echo ok"}')],
            usage_tokens=10),
        CompletionResult(text="done", usage_tokens=20),
    ])
    await actor.post_message("run echo")
    await wait_idle(actor)
    evs = actor.log.read()
    started = next(e for e in evs if e.type == "tool_call_started")
    finished = next(e for e in evs if e.type == "tool_call_finished")
    assert started.auto_approved and started.display == "echo ok"
    assert "ok" in finished.output and not finished.is_error
    # second LLM call saw the tool result
    assert llm.calls[1][-1]["role"] == "tool"


async def test_tool_error_feeds_back_and_run_continues(tmp_path):
    actor, _ = make_actor(tmp_path, [
        CompletionResult(text="", tool_calls=[
            ToolCallSpec(id="c1", name="read_file", arguments='{"path": "nope"}')],
            usage_tokens=10),
        CompletionResult(text="recovered", usage_tokens=20),
    ])
    await actor.post_message("go")
    await wait_idle(actor)
    evs = actor.log.read()
    fin = next(e for e in evs if e.type == "tool_call_finished")
    assert fin.is_error
    assert evs[-2].type == "run_finished" and evs[-2].reason == "completed"


async def test_llm_error_ends_run_with_error(tmp_path):
    actor, _ = make_actor(tmp_path, [LLMError("proxy down")])
    await actor.post_message("go")
    await wait_idle(actor)
    assert "error" in types(actor)
    fin = [e for e in actor.log.read() if e.type == "run_finished"]
    assert fin[0].reason == "error" and actor.meta.status == "idle"


async def test_unexpected_error_ends_run_with_error(tmp_path):
    actor, _ = make_actor(tmp_path, [RuntimeError("boom")])
    await actor.post_message("go")
    await wait_idle(actor)  # backstop caught it → task did not raise
    assert "error" in types(actor)
    err = next(e for e in actor.log.read() if e.type == "error")
    assert "boom" in err.message
    fin = [e for e in actor.log.read() if e.type == "run_finished"]
    assert fin[0].reason == "error" and actor.meta.status == "idle"


async def test_abnormal_run_exit_closes_dangling(tmp_path):
    # The assistant tool_calls are emitted, then dispatch crashes uncaught
    # (e.g. the old display() AttributeError). The backstop must close the
    # dangling tool_use so history stays valid (Anthropic 400: tool_use
    # without tool_result).
    actor, _ = make_actor(tmp_path, [
        CompletionResult(text="", tool_calls=[
            ToolCallSpec(id="c1", name="bash", arguments='{"command": "echo hi"}')],
            usage_tokens=10),
    ])

    async def boom(_call, _completion_model=None):
        raise RuntimeError("dispatch crashed")

    actor._execute_call = boom
    await actor.post_message("go")
    await wait_idle(actor)
    from forge.engine.projection import dangling_call_ids
    assert dangling_call_ids(actor.log.read()) == []
    fin = next(e for e in actor.log.read()
               if e.type == "tool_call_finished" and e.call_id == "c1")
    assert fin.is_error
    assert any(e.type == "run_finished" and e.reason == "error"
               for e in actor.log.read())


async def test_new_message_heals_dangling_from_prior_crash(tmp_path):
    # A run died leaving an unresolved tool_use (process restart, uncaught
    # crash). The next run started by post_message heals it in _run before the
    # LLM request.
    actor, _ = make_actor(tmp_path, [CompletionResult(text="ok", usage_tokens=1)])
    actor.emit(actor._e(
        AssistantMessage, text="", tool_calls=[
            ToolCallSpec(id="orphan", name="bash", arguments="{}")],
        usage_tokens=1))
    from forge.engine.projection import dangling_call_ids
    assert dangling_call_ids(actor.log.read()) == [("orphan", "bash")]
    await actor.post_message("continue")
    await wait_idle(actor)
    assert dangling_call_ids(actor.log.read()) == []
    fin = next(e for e in actor.log.read()
               if e.type == "tool_call_finished" and e.call_id == "orphan")
    assert fin.is_error


async def test_cancel_while_queued_finishes_and_idles(tmp_path):
    # Two actors share one slot; the second is cancelled while still queued
    # (awaiting the semaphore). It must still emit run_finished and go idle.
    sched = Scheduler(1)

    def build(name, script, delay=0.0):
        (tmp_path / name).mkdir(exist_ok=True)
        meta = SessionMeta(id=name, cwd=str(tmp_path / name), model="m")
        actor = SessionActor(
            meta=meta, home=tmp_path / "home", config=ForgeConfig(),
            llm=FakeLLM(script, delay=delay), bus=EventBus(), scheduler=sched,
            system_prompt_fn=lambda m: "SYS")
        return actor

    a = build("a", [CompletionResult(text="slow", usage_tokens=1)], delay=0.5)
    b = build("b", [CompletionResult(text="fast", usage_tokens=1)])
    await a.post_message("hold the slot")
    await asyncio.sleep(0.05)
    await b.post_message("queue me")
    await asyncio.sleep(0.05)
    assert b.meta.status == "queued"
    b.cancel()  # user cancels the queued run via POST /cancel
    await asyncio.gather(a.run_task, b.run_task, return_exceptions=True)
    fin = [e for e in b.log.read() if e.type == "run_finished"]
    assert fin and fin[0].reason == "cancelled", "no run_finished after queued cancel"
    assert b.meta.status == "idle", f"stuck in {b.meta.status!r}"


async def test_message_during_final_stream_is_consumed(tmp_path):
    actor, llm = make_actor(tmp_path, [
        CompletionResult(text="first", usage_tokens=10),
        CompletionResult(text="second", usage_tokens=20),
    ], delay=0.1)  # hold each completion in flight
    await actor.post_message("one")
    await asyncio.sleep(0.05)          # let the run start its first LLM call
    await actor.post_message("two")    # lands mid-flight → must not be dropped
    await wait_idle(actor)
    assert len(llm.calls) == 2  # loop continued instead of finishing
    assert [e.text for e in actor.log.read() if e.type == "assistant_message"] == [
        "first", "second"]


async def test_tool_call_pending_published_before_started(tmp_path):
    actor, llm = make_actor(tmp_path, [
        CompletionResult(text="", tool_calls=[
            ToolCallSpec(id="c1", name="bash", arguments='{"command": "echo ok"}')],
            usage_tokens=10),
        CompletionResult(text="done", usage_tokens=20),
    ])
    q = actor.bus.subscribe()
    await actor.post_message("run echo")
    await wait_idle(actor)
    seen = []
    while not q.empty():
        seen.append(q.get_nowait())
    pending = next(e for e in seen if e.type == "tool_call_pending")
    assert pending.call_id == "c1" and pending.tool == "bash" and pending.seq == 0
    order = [e.type for e in seen]
    assert order.index("tool_call_pending") < order.index("tool_call_started")

async def test_skill_gated_tool_hidden_until_skill_loaded(tmp_path):
    from forge.engine.events import ToolCallFinished, ToolCallStarted
    meta = SessionMeta(id="s1", cwd=str(tmp_path / "ws"), model="m")
    (tmp_path / "ws").mkdir(exist_ok=True)
    actor = SessionActor(
        meta=meta, home=tmp_path / "home",
        config=ForgeConfig(openrouter_api_key="sk-or"),
        llm=FakeLLM([]), bus=EventBus(), scheduler=Scheduler(3),
        system_prompt_fn=lambda m: "SYS")
    assert "create_image" in actor.tools
    assert actor.skill_gated.get("create_image") == "image-generation"
    # Hidden before the skill is loaded.
    assert "create_image" not in {t.name for t in actor._active_tools()}
    # A successful load_skill pair activates it.
    actor.emit(actor._e(ToolCallStarted, call_id="c1", tool="load_skill",
                        display="image-generation"))
    actor.emit(actor._e(ToolCallFinished, call_id="c1", tool="load_skill",
                        output="body"))
    assert "create_image" in {t.name for t in actor._active_tools()}


async def test_completed_run_marks_meta_unread_live(tmp_path):
    actor, _ = make_actor(tmp_path, [CompletionResult(text="hi!", usage_tokens=10)])
    await actor.post_message("hello")
    await wait_idle(actor)
    fin = next(e for e in actor.log.read() if e.type == "run_finished")
    assert fin.reason == "completed" and fin.unread
    # emit() refreshed the cached pill state on meta without a reload.
    assert actor.meta.unread and actor.meta.last_run_reason == "completed"
    assert actor.meta.last_run_seq == fin.seq


async def test_acknowledge_clears_unread_and_is_idempotent(tmp_path):
    actor, _ = make_actor(tmp_path, [CompletionResult(text="hi!", usage_tokens=10)])
    await actor.post_message("hello")
    await wait_idle(actor)
    assert actor.meta.unread

    actor.acknowledge()
    assert not actor.meta.unread
    acks = [e for e in actor.log.read() if e.type == "run_acknowledged"]
    assert len(acks) == 1

    # Repeated acks (or acking an already-read session) emit nothing.
    actor.acknowledge()
    assert [e for e in actor.log.read() if e.type == "run_acknowledged"] == acks
