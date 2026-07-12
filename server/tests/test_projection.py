from forge.engine.events import (
    AssistantMessage, ContextCompacted, HistoryRewound, RunAcknowledged,
    RunFinished, ToolCallFinished, ToolCallSpec, ToolCallStarted, UserMessage,
)
from forge.engine.projection import (
    dangling_call_ids, loaded_skill_names, to_messages, unread_run_seq,
)

S = dict(session_id="s1", ts=0.0)


def test_basic_turn_with_tools():
    events = [
        UserMessage(seq=1, **S, text="run tests"),
        AssistantMessage(seq=2, **S, text="", tool_calls=[
            ToolCallSpec(id="c1", name="bash", arguments='{"command": "pytest"}')]),
        ToolCallFinished(seq=3, **S, call_id="c1", tool="bash", output="1 passed"),
        AssistantMessage(seq=4, **S, text="All green."),
    ]
    msgs = to_messages(events, "SYS", "claude-opus-4-8")
    assert msgs[0] == {"role": "system", "content": "SYS"}
    # proxy-proof mirror of the system prompt, prefixed to the first user message
    assert msgs[1]["role"] == "user"
    assert "SYS" in msgs[1]["content"] and msgs[1]["content"].endswith("run tests")
    assert msgs[2]["role"] == "assistant" and msgs[2]["content"] is None
    assert msgs[2]["tool_calls"][0] == {
        "id": "c1", "type": "function",
        "function": {"name": "bash", "arguments": '{"command": "pytest"}'}}
    assert msgs[3] == {"role": "tool", "tool_call_id": "c1", "content": "1 passed"}
    assert msgs[4] == {"role": "assistant", "content": "All green."}


def test_tool_result_with_images_projects_multimodal():
    events = [
        UserMessage(seq=1, **S, text="show me the pdf"),
        AssistantMessage(seq=2, **S, text="", tool_calls=[
            ToolCallSpec(id="c1", name="view", arguments='{"path": "x.pdf"}')]),
        ToolCallFinished(seq=3, **S, call_id="c1", tool="view",
                         output="Rendered 1 page(s).",
                         images=["data:image/png;base64,AAAA"]),
    ]
    msgs = to_messages(events, "SYS")
    tool_msg = msgs[3]
    assert tool_msg["role"] == "tool" and tool_msg["tool_call_id"] == "c1"
    assert tool_msg["content"] == [
        {"type": "text", "text": "Rendered 1 page(s)."},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
    ]


def test_tool_result_without_images_stays_plain_string():
    events = [
        UserMessage(seq=1, **S, text="go"),
        AssistantMessage(seq=2, **S, text="", tool_calls=[
            ToolCallSpec(id="c1", name="bash", arguments="{}")]),
        ToolCallFinished(seq=3, **S, call_id="c1", tool="bash", output="ok"),
    ]
    msgs = to_messages(events, "SYS")
    assert msgs[3] == {"role": "tool", "tool_call_id": "c1", "content": "ok"}


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
    assert msgs[4]["content"] == "also update docs"  # steering msg gets no prefix


def test_compaction_cuts_and_injects_summary():
    events = [
        UserMessage(seq=1, **S, text="old"),
        AssistantMessage(seq=2, **S, text="old reply"),
        ContextCompacted(seq=3, **S, summary="did old things", upto_seq=2),
        UserMessage(seq=4, **S, text="new"),
    ]
    msgs = to_messages(events, "SYS", "claude-opus-4-8")
    # summary is the first user message post-compaction, so it carries the mirror
    assert msgs[1]["role"] == "user" and "did old things" in msgs[1]["content"]
    assert "SYS" in msgs[1]["content"]
    assert msgs[2] == {"role": "user", "content": "new"}
    assert len(msgs) == 3


def test_non_claude_model_skips_prompt_mirror():
    events = [
        UserMessage(seq=1, **S, text="run tests"),
        AssistantMessage(seq=2, **S, text="ok"),
    ]
    msgs = to_messages(events, "SYS", "gpt-5.2")
    assert msgs[0] == {"role": "system", "content": "SYS"}
    # non-cloaked executor keeps the system message, so no <context> mirror
    assert msgs[1] == {"role": "user", "content": "run tests"}
    assert "<context>" not in str(msgs)


def test_dangling_call_ids():
    events = [
        AssistantMessage(seq=1, **S, text="", tool_calls=[
            ToolCallSpec(id="c1", name="bash", arguments="{}"),
            ToolCallSpec(id="c2", name="read_file", arguments="{}")]),
        ToolCallFinished(seq=2, **S, call_id="c1", tool="bash", output="ok"),
    ]
    assert dangling_call_ids(events) == [("c2", "read_file")]


def test_loaded_skill_names_pairs_start_and_finish():
    events = [
        UserMessage(seq=1, **S, text="make a logo"),
        ToolCallStarted(seq=2, **S, call_id="c1", tool="load_skill",
                        display="image-generation"),
        ToolCallFinished(seq=3, **S, call_id="c1", tool="load_skill", output="body"),
        ToolCallStarted(seq=4, **S, call_id="c2", tool="load_skill", display="pdf"),
        ToolCallFinished(seq=5, **S, call_id="c2", tool="load_skill",
                         output="err", is_error=True),
    ]
    assert loaded_skill_names(events) == {"image-generation"}  # errored load excluded


def test_loaded_skill_names_empty_without_load():
    events = [UserMessage(seq=1, **S, text="hi")]
    assert loaded_skill_names(events) == set()


def test_unread_run_seq_flags_completed_unread_run():
    events = [
        UserMessage(seq=1, **S, text="go"),
        AssistantMessage(seq=2, **S, text="done"),
        RunFinished(seq=3, **S, reason="completed", unread=True),
    ]
    assert unread_run_seq(events) == 3


def test_unread_run_seq_cleared_by_acknowledge():
    events = [
        UserMessage(seq=1, **S, text="go"),
        RunFinished(seq=2, **S, reason="completed", unread=True),
        RunAcknowledged(seq=3, **S, run_seq=2),
    ]
    assert unread_run_seq(events) is None


def test_unread_run_seq_ignores_non_success_completions():
    for reason in ("error", "interrupted", "cancelled"):
        events = [
            UserMessage(seq=1, **S, text="go"),
            RunFinished(seq=2, **S, reason=reason, unread=True),
        ]
        assert unread_run_seq(events) is None, reason


def test_unread_run_seq_old_logs_default_read():
    # run_finished predating the feature carries no ``unread`` flag → stays read.
    events = [
        UserMessage(seq=1, **S, text="go"),
        RunFinished(seq=2, **S, reason="completed"),
    ]
    assert unread_run_seq(events) is None


def test_unread_run_seq_rewind_drops_abandoned_unread():
    # An unread completion on a branch the rewind discards is not unread; only
    # the completion on the surviving active branch counts.
    events = [
        UserMessage(seq=1, **S, text="one",
                    workspace_checkpoint="cp1"),
        RunFinished(seq=2, **S, reason="completed", unread=True),
        UserMessage(seq=3, **S, text="two",
                    workspace_checkpoint="cp2"),
        RunFinished(seq=4, **S, reason="completed", unread=True),
        HistoryRewound(seq=5, **S, target_user_seq=3,
                       target_checkpoint="cp2", safety_checkpoint="safety",
                       replacement=False),
    ]
    # After rewinding to seq 3, its branch (incl. run_finished seq 4) is dropped;
    # only the seq-2 completion survives on the active branch.
    assert unread_run_seq(events) == 2
