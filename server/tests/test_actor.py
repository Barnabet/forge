import asyncio

from forge.engine.actor import SessionActor, SessionMeta
from forge.engine.bus import EventBus
from forge.engine.events import ToolCallSpec
from forge.engine.scheduler import Scheduler
from forge.llm.base import CompletionResult, LLMError
from forge.llm.fake import FakeLLM
from forge.store.config import ForgeConfig


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
        "user_message", "session_renamed", "status_changed",  # running
        "assistant_message", "run_finished", "status_changed"]  # idle
    assert actor.meta.name == "hello" and actor.meta.status == "idle"
    assert llm.calls[0][0]["content"] == "SYS"


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
