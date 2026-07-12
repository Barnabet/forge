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
    assert len(final_msgs) == 2  # system + summary-as-user (with mirror prefix) only
    # projection helper agrees
    msgs = to_messages(evs, "SYS")
    assert not any(m["role"] == "tool" for m in msgs)


async def test_compaction_publishes_running_and_done(tmp_path):
    actor, _ = make_actor(tmp_path, [
        CompletionResult(text="hi", usage_tokens=5),
        CompletionResult(text="the summary", usage_tokens=5),  # summarizer
    ])
    await actor.post_message("go")
    await wait_idle(actor)
    q = actor.bus.subscribe()
    assert await actor.compact_now()
    seen = []
    while not q.empty():
        seen.append(q.get_nowait())
    states = [e.state for e in seen if e.type == "compaction"]
    assert states == ["running", "done"]


async def test_message_during_compaction_is_held_and_reaches_model(tmp_path):
    # A message posted while a manual compaction's summarizer is in flight must
    # be ghosted (steering), not spawn a second summarizer, and reach the model.
    actor, llm = make_actor(tmp_path, [
        CompletionResult(text="hi", usage_tokens=5),
        CompletionResult(text="the summary", usage_tokens=5),  # summarizer (slow)
        CompletionResult(text="done", usage_tokens=10),
    ], delay=0.3)
    await actor.post_message("go")
    await asyncio.sleep(0.45)  # first completion done, sit idle
    compact = asyncio.create_task(actor.compact_now())
    await asyncio.sleep(0.1)  # summarizer now in flight
    await actor.post_message("HELD-MSG")  # arrives during compaction
    held = next(e for e in actor.log.read()
                if e.type == "user_message" and e.text == "HELD-MSG")
    assert held.steering is True
    await compact
    await wait_idle(actor)
    flat = str(to_messages(actor.log.read(), "SYS"))
    assert "HELD-MSG" in flat, "held message dropped from model context"


async def test_second_compaction_while_active_is_refused(tmp_path):
    actor, _ = make_actor(tmp_path, [
        CompletionResult(text="hi", usage_tokens=5),
        CompletionResult(text="the summary", usage_tokens=5),  # summarizer (slow)
    ], delay=0.3)
    await actor.post_message("go")
    await asyncio.sleep(0.45)
    first = asyncio.create_task(actor.compact_now())
    await asyncio.sleep(0.1)  # first compaction in flight
    assert await actor.compact_now() is False
    assert await first is True


async def test_compaction_prompt_is_structured(tmp_path):
    actor, llm = make_actor(tmp_path, [
        CompletionResult(text="hi", usage_tokens=5),
        CompletionResult(text="the summary", usage_tokens=5),  # summarizer
    ])
    await actor.post_message("go")
    await wait_idle(actor)
    assert await actor.compact_now()
    prompt = llm.calls[-1][0]["content"]
    for header in ("Primary Request and Intent", "Errors and fixes",
                   "All user messages", "Optional Next Step"):
        assert header in prompt


async def test_compaction_summary_strips_analysis(tmp_path):
    actor, _ = make_actor(tmp_path, [
        CompletionResult(text="hi", usage_tokens=5),
        CompletionResult(
            text="<analysis>scratch work</analysis>\n"
                 "<summary>\n1. Primary Request and Intent: do X\n</summary>",
            usage_tokens=5),
    ])
    await actor.post_message("go")
    await wait_idle(actor)
    assert await actor.compact_now()
    comp = next(e for e in actor.log.read() if e.type == "context_compacted")
    assert "scratch work" not in comp.summary
    assert "Primary Request and Intent" in comp.summary


async def test_compaction_publishes_section_phases(tmp_path):
    section_text = "<summary>\n" + "\n".join(
        f"{i}. {name}: body" for i, name in enumerate(
            ["Primary Request and Intent", "Key Technical Concepts",
             "Files and Code Sections", "Errors and fixes", "Problem Solving",
             "All user messages", "Pending Tasks", "Current Work",
             "Optional Next Step"], start=1)) + "\n</summary>"
    actor, _ = make_actor(tmp_path, [
        CompletionResult(text="hi", usage_tokens=5),
        CompletionResult(text=section_text, usage_tokens=5),  # summarizer
    ])
    await actor.post_message("go")
    await wait_idle(actor)
    q = actor.bus.subscribe()
    assert await actor.compact_now()
    phases = [e.phase for e in _drain(q) if e.type == "compaction" and e.state == "running"]
    # starts at 0 (Analyzing), then advances monotonically to all 9 sections
    assert phases[0] == 0
    assert phases[-1] == 9
    assert phases == sorted(phases)


def _drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


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
