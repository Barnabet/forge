from forge.engine.events import (
    AssistantMessage, ContextCompacted, ToolCallFinished, ToolCallSpec, UserMessage,
)
from forge.engine.projection import dangling_call_ids, to_messages

S = dict(session_id="s1", ts=0.0)


def test_basic_turn_with_tools():
    events = [
        UserMessage(seq=1, **S, text="run tests"),
        AssistantMessage(seq=2, **S, text="", tool_calls=[
            ToolCallSpec(id="c1", name="bash", arguments='{"command": "pytest"}')]),
        ToolCallFinished(seq=3, **S, call_id="c1", tool="bash", output="1 passed"),
        AssistantMessage(seq=4, **S, text="All green."),
    ]
    msgs = to_messages(events, "SYS")
    assert msgs[0] == {"role": "system", "content": "SYS"}
    assert msgs[1] == {"role": "user", "content": "run tests"}
    assert msgs[2]["role"] == "assistant" and msgs[2]["content"] is None
    assert msgs[2]["tool_calls"][0] == {
        "id": "c1", "type": "function",
        "function": {"name": "bash", "arguments": '{"command": "pytest"}'}}
    assert msgs[3] == {"role": "tool", "tool_call_id": "c1", "content": "1 passed"}
    assert msgs[4] == {"role": "assistant", "content": "All green."}


def test_steering_message_lands_after_open_tool_block():
    events = [
        UserMessage(seq=1, **S, text="go"),
        AssistantMessage(seq=2, **S, text="", tool_calls=[
            ToolCallSpec(id="c1", name="bash", arguments="{}")]),
        UserMessage(seq=3, **S, text="also update docs"),  # arrived mid-tool
        ToolCallFinished(seq=4, **S, call_id="c1", tool="bash", output="ok"),
    ]
    msgs = to_messages(events, "SYS")
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "tool", "user"]
    assert msgs[4]["content"] == "also update docs"


def test_compaction_cuts_and_injects_summary():
    events = [
        UserMessage(seq=1, **S, text="old"),
        AssistantMessage(seq=2, **S, text="old reply"),
        ContextCompacted(seq=3, **S, summary="did old things", upto_seq=2),
        UserMessage(seq=4, **S, text="new"),
    ]
    msgs = to_messages(events, "SYS")
    assert msgs[1]["role"] == "user" and "did old things" in msgs[1]["content"]
    assert msgs[2] == {"role": "user", "content": "new"}
    assert len(msgs) == 3


def test_dangling_call_ids():
    events = [
        AssistantMessage(seq=1, **S, text="", tool_calls=[
            ToolCallSpec(id="c1", name="bash", arguments="{}"),
            ToolCallSpec(id="c2", name="read_file", arguments="{}")]),
        ToolCallFinished(seq=2, **S, call_id="c1", tool="bash", output="ok"),
    ]
    assert dangling_call_ids(events) == [("c2", "read_file")]
