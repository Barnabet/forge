from forge.engine.events import UserMessage, ToolCallFinished, parse_event


def test_event_roundtrip():
    e = UserMessage(session_id="s1", ts=1.0, text="hi")
    d = e.model_dump(mode="json")
    assert d["type"] == "user_message" and d["seq"] == 0
    assert parse_event(d) == e


def test_discriminated_parse():
    d = {"seq": 3, "session_id": "s1", "ts": 2.0, "type": "tool_call_finished",
         "call_id": "c1", "tool": "bash", "output": "ok", "is_error": False,
         "duration_ms": 12, "diff_stats": None}
    e = parse_event(d)
    assert isinstance(e, ToolCallFinished) and e.seq == 3 and e.output == "ok"
