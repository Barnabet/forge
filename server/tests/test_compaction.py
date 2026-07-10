import asyncio

from forge.engine.events import ToolCallSpec
from forge.engine.projection import to_messages
from forge.llm.base import CompletionResult
from forge.store.config import ForgeConfig, ModelConfig

from tests.test_actor import make_actor, wait_idle


async def test_compaction_triggers_and_projection_shrinks(tmp_path):
    # tiny context window so the first tool turn crosses 75%
    actor, llm = make_actor(tmp_path, [
        CompletionResult(text="", tool_calls=[
            ToolCallSpec(id="c1", name="bash", arguments='{"command": "echo x"}')],
            usage_tokens=90),                                   # 90 > 0.75 * 100
        CompletionResult(text="summary of session", usage_tokens=5),  # summarizer call
        CompletionResult(text="done", usage_tokens=10),
    ])
    # display_name is a required field on ModelConfig; the brief omitted it, which
    # raises ValidationError before any compaction runs. Supplied here — irrelevant
    # to what this test verifies (no production change; see task-13 report).
    actor.config = ForgeConfig(
        models=[ModelConfig(id="m", display_name="m", context_window=100)])
    await actor.post_message("go")
    await wait_idle(actor)
    evs = actor.log.read()
    comp = next(e for e in evs if e.type == "context_compacted")
    assert comp.summary == "summary of session"
    # the LLM call AFTER compaction starts from the summary, not raw history
    final_msgs = llm.calls[2]
    assert "summary of session" in final_msgs[1]["content"]
    assert len(final_msgs) == 2  # system + summary-as-user only
    # projection helper agrees
    msgs = to_messages(evs, "SYS")
    assert not any(m["role"] == "tool" for m in msgs)


async def test_steering_during_compaction_summary_reaches_model(tmp_path):
    # A user_message posted while the summarizer LLM call is in flight must land
    # AFTER the compaction cut, or it is silently dropped from the model context.
    actor, llm = make_actor(tmp_path, [
        CompletionResult(text="", tool_calls=[
            ToolCallSpec(id="c1", name="bash", arguments='{"command": "echo x"}')],
            usage_tokens=90),                    # crosses 75% of 100 -> compaction
        CompletionResult(text="the summary", usage_tokens=5),   # summarizer (slow)
        CompletionResult(text="done", usage_tokens=10),
        CompletionResult(text="done2", usage_tokens=10),
    ], delay=0.3)
    actor.config = ForgeConfig(
        models=[ModelConfig(id="m", display_name="m", context_window=100)])
    await actor.post_message("go")
    await asyncio.sleep(0.45)          # call1 0-0.3, tool ~0.31, summarizer 0.31-0.61
    await actor.post_message("STEER-ME")   # arrives during summarization
    await wait_idle(actor)
    flat = str(to_messages(actor.log.read(), "SYS"))
    assert "STEER-ME" in flat, "steering message dropped from model context"
