import asyncio

from forge.engine.events import ToolCallSpec
from forge.llm.base import CompletionResult
from forge.store.config import load_config

from tests.test_actor import make_actor, wait_idle

BASH_CALL = CompletionResult(text="", tool_calls=[
    ToolCallSpec(id="c1", name="bash", arguments='{"command": "echo hi"}')],
    usage_tokens=10)
DONE = CompletionResult(text="done", usage_tokens=20)


async def pump_until(actor, etype, timeout=5):
    async with asyncio.timeout(timeout):
        while not any(e.type == etype for e in actor.log.read()):
            await asyncio.sleep(0.01)


async def test_guarded_allow(tmp_path):
    actor, _ = make_actor(tmp_path, [BASH_CALL, DONE], autonomy="guarded")
    await actor.post_message("go")
    await pump_until(actor, "approval_requested")
    assert actor.meta.status == "attention"
    await actor.resolve_approval("c1", "allow")
    await wait_idle(actor)
    evs = actor.log.read()
    assert next(e for e in evs if e.type == "approval_resolved").decision == "allow"
    started = next(e for e in evs if e.type == "tool_call_started")
    assert not started.auto_approved


async def test_guarded_deny_feeds_model(tmp_path):
    actor, llm = make_actor(tmp_path, [BASH_CALL, DONE], autonomy="guarded")
    await actor.post_message("go")
    await pump_until(actor, "approval_requested")
    await actor.resolve_approval("c1", "deny")
    await wait_idle(actor)
    fin = next(e for e in actor.log.read() if e.type == "tool_call_finished")
    assert fin.is_error and "denied" in fin.output.lower()
    assert llm.calls[1][-1]["role"] == "tool"  # denial visible to model
    assert not any(e.type == "tool_call_started" for e in actor.log.read())


async def test_always_global_persists_policy(tmp_path):
    actor, _ = make_actor(tmp_path, [BASH_CALL, DONE, BASH_CALL, DONE],
                          autonomy="guarded")
    await actor.post_message("go")
    await pump_until(actor, "approval_requested")
    await actor.resolve_approval(
        "c1", "allow", always={"pattern": "echo *", "scope": "global"})
    await wait_idle(actor)
    assert any(e.type == "policy_added" and e.scope == "global"
               for e in actor.log.read())
    assert any(p.pattern == "echo *" for p in load_config(tmp_path / "home").policies)
    # second identical call sails through without a gate
    await actor.post_message("again")
    await wait_idle(actor)
    assert sum(1 for e in actor.log.read() if e.type == "approval_requested") == 1


async def test_cancel_mid_gate_closes_dangling(tmp_path):
    actor, _ = make_actor(tmp_path, [BASH_CALL], autonomy="guarded")
    await actor.post_message("go")
    await pump_until(actor, "approval_requested")
    actor.cancel()
    await asyncio.wait_for(asyncio.gather(actor.run_task, return_exceptions=True), 5)
    evs = actor.log.read()
    assert any(e.type == "run_finished" and e.reason == "cancelled" for e in evs)
    fin = next(e for e in evs if e.type == "tool_call_finished")
    assert "cancelled" in fin.output.lower()
    assert actor.meta.status == "idle"
