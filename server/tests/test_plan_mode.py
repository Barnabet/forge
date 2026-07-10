import asyncio
import json

from forge.engine.actor import SessionActor, SessionMeta
from forge.engine.bus import EventBus
from forge.engine.events import ToolCallSpec
from forge.engine.scheduler import Scheduler
from forge.llm.base import CompletionResult
from forge.llm.fake import FakeLLM
from forge.store.config import ForgeConfig


def make_actor(tmp_path, script, mode="act"):
    meta = SessionMeta(id="s1", cwd=str(tmp_path / "ws"), model="m",
                       autonomy="yolo", mode=mode)
    (tmp_path / "ws").mkdir(exist_ok=True)
    llm = FakeLLM(script)
    actor = SessionActor(
        meta=meta, home=tmp_path / "home", config=ForgeConfig(),
        llm=llm, bus=EventBus(), scheduler=Scheduler(3),
        system_prompt_fn=lambda m: "SYS")
    return actor, llm


async def wait_idle(actor):
    await asyncio.wait_for(actor.run_task, timeout=5)


async def wait_status(actor, status):
    for _ in range(200):
        if actor.meta.status == status:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"never reached status {status}")


def plan_call(plan="# The plan", call_id="p1"):
    return ToolCallSpec(id=call_id, name="propose_plan",
                        arguments=json.dumps({"plan": plan}))


async def test_set_mode_emits_and_is_idempotent(tmp_path):
    actor, _ = make_actor(tmp_path, [])
    actor.set_mode("plan")
    actor.set_mode("plan")
    evs = [e for e in actor.log.read() if e.type == "mode_changed"]
    assert len(evs) == 1 and evs[0].mode == "plan"
    assert actor.meta.mode == "plan"


async def test_plan_mode_filters_tool_specs(tmp_path):
    actor, _ = make_actor(tmp_path, [], mode="plan")
    names = {t.name for t in actor._active_tools()}
    assert "propose_plan" in names and "update_todos" in names
    assert "read_file" in names and "spawn_agents" in names
    assert "bash" not in names and "write_file" not in names and "edit_file" not in names
    actor.set_mode("act")
    assert "bash" in {t.name for t in actor._active_tools()}


async def test_write_tool_blocked_in_plan_mode(tmp_path):
    actor, _ = make_actor(tmp_path, [
        CompletionResult(text="", tool_calls=[
            ToolCallSpec(id="c1", name="write_file",
                         arguments='{"path": "x", "content": "y"}')]),
        CompletionResult(text="ok"),
    ], mode="plan")
    await actor.post_message("go")
    await wait_idle(actor)
    fin = next(e for e in actor.log.read() if e.type == "tool_call_finished")
    assert fin.is_error and "plan mode" in fin.output
    last = actor.log.read()[-2]
    assert last.type == "run_finished" and last.reason == "completed"


async def test_plan_approve_flips_mode_and_continues(tmp_path):
    actor, _ = make_actor(tmp_path, [
        CompletionResult(text="", tool_calls=[plan_call()]),
        CompletionResult(text="executing"),
    ], mode="plan")
    await actor.post_message("plan it")
    await wait_status(actor, "attention")
    proposed = [e for e in actor.log.read() if e.type == "plan_proposed"]
    assert proposed and proposed[0].plan == "# The plan" and proposed[0].call_id == "p1"
    await actor.resolve_plan("p1", "approve")
    await wait_idle(actor)
    evs = actor.log.read()
    resolved = next(e for e in evs if e.type == "plan_resolved")
    assert resolved.decision == "approve"
    assert any(e.type == "mode_changed" and e.mode == "act" for e in evs)
    assert actor.meta.mode == "act"
    fin = next(e for e in evs if e.type == "tool_call_finished" and e.call_id == "p1")
    assert "Plan approved" in fin.output and not fin.is_error


async def test_plan_revise_keeps_plan_mode_with_feedback(tmp_path):
    actor, llm = make_actor(tmp_path, [
        CompletionResult(text="", tool_calls=[plan_call()]),
        CompletionResult(text="revised plan coming up"),
    ], mode="plan")
    await actor.post_message("plan it")
    await wait_status(actor, "attention")
    await actor.resolve_plan("p1", "revise", feedback="use sqlite instead")
    await wait_idle(actor)
    evs = actor.log.read()
    resolved = next(e for e in evs if e.type == "plan_resolved")
    assert resolved.decision == "revise" and resolved.feedback == "use sqlite instead"
    assert actor.meta.mode == "plan"
    assert not any(e.type == "mode_changed" for e in evs)
    fin = next(e for e in evs if e.type == "tool_call_finished" and e.call_id == "p1")
    assert "use sqlite instead" in fin.output and not fin.is_error
    # the model saw the feedback as the tool result
    assert llm.calls[1][-1]["role"] == "tool"
    assert "use sqlite instead" in llm.calls[1][-1]["content"]


async def test_cancel_while_plan_gated_closes_dangling(tmp_path):
    actor, _ = make_actor(tmp_path, [
        CompletionResult(text="", tool_calls=[plan_call()]),
    ], mode="plan")
    await actor.post_message("plan it")
    await wait_status(actor, "attention")
    actor.cancel()
    try:
        await actor.run_task
    except asyncio.CancelledError:
        pass
    evs = actor.log.read()
    fin = next(e for e in evs if e.type == "tool_call_finished" and e.call_id == "p1")
    assert fin.is_error
    assert any(e.type == "run_finished" and e.reason == "cancelled" for e in evs)
    assert actor.meta.mode == "plan"


async def test_empty_plan_is_error_result(tmp_path):
    actor, _ = make_actor(tmp_path, [
        CompletionResult(text="", tool_calls=[
            ToolCallSpec(id="p1", name="propose_plan", arguments='{"plan": "  "}')]),
        CompletionResult(text="ok"),
    ], mode="plan")
    await actor.post_message("plan it")
    await wait_idle(actor)
    fin = next(e for e in actor.log.read() if e.type == "tool_call_finished")
    assert fin.is_error and "non-empty" in fin.output
    assert not any(e.type == "plan_proposed" for e in actor.log.read())


async def test_update_todos_emits_snapshot(tmp_path):
    todos = [{"text": "step one", "status": "completed"},
             {"text": "step two", "status": "in_progress"}]
    actor, _ = make_actor(tmp_path, [
        CompletionResult(text="", tool_calls=[
            ToolCallSpec(id="t1", name="update_todos",
                         arguments=json.dumps({"todos": todos}))]),
        CompletionResult(text="ok"),
    ])
    await actor.post_message("go")
    await wait_idle(actor)
    evs = actor.log.read()
    fin_i = next(i for i, e in enumerate(evs) if e.type == "tool_call_finished")
    upd = next(e for e in evs if e.type == "todos_updated")
    assert evs.index(upd) == fin_i + 1
    assert [(t.text, t.status) for t in upd.todos] == [
        ("step one", "completed"), ("step two", "in_progress")]


async def test_invalid_todos_no_event(tmp_path):
    actor, _ = make_actor(tmp_path, [
        CompletionResult(text="", tool_calls=[
            ToolCallSpec(id="t1", name="update_todos",
                         arguments='{"todos": [{"text": "x", "status": "doing"}]}')]),
        CompletionResult(text="ok"),
    ])
    await actor.post_message("go")
    await wait_idle(actor)
    evs = actor.log.read()
    fin = next(e for e in evs if e.type == "tool_call_finished")
    assert fin.is_error and "invalid status" in fin.output
    assert not any(e.type == "todos_updated" for e in evs)


async def test_subagents_forced_read_only_in_plan_mode(tmp_path):
    args = {"tasks": [{"task": "investigate", "mode": "write"}]}
    actor, llm = make_actor(tmp_path, [
        CompletionResult(text="", tool_calls=[
            ToolCallSpec(id="s1", name="spawn_agents", arguments=json.dumps(args))]),
        CompletionResult(text="ok"),
    ], mode="plan")
    # worker completion for the single subagent task
    llm.script.insert(1, CompletionResult(text="report"))
    await actor.post_message("go")
    await wait_idle(actor)
    started = next(e for e in actor.log.read() if e.type == "tool_call_started")
    assert "0 with write access" in started.display
    # the worker prompt declared read mode
    worker_sys = llm.calls[1][0]["content"]
    assert "Access mode: read" in worker_sys
