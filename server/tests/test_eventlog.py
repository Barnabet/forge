from forge.engine.events import UserMessage
from forge.store.eventlog import EventLog


def test_append_assigns_seq_and_persists(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")
    e1 = log.append(UserMessage(session_id="s1", ts=1.0, text="a"))
    e2 = log.append(UserMessage(session_id="s1", ts=2.0, text="b"))
    assert (e1.seq, e2.seq) == (1, 2) and log.last_seq == 2

    reloaded = EventLog(tmp_path / "events.jsonl")
    assert [e.text for e in reloaded.read()] == ["a", "b"]
    assert reloaded.last_seq == 2
    assert [e.seq for e in reloaded.read(after_seq=1)] == [2]


def test_torn_trailing_line_is_dropped(tmp_path):
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    log.append(UserMessage(session_id="s1", ts=1.0, text="a"))
    log.append(UserMessage(session_id="s1", ts=2.0, text="b"))
    with path.open("a") as f:
        f.write('{"type": "user_message", "session_id": "s1", "ts"')  # crash mid-append
    reloaded = EventLog(path)
    assert [e.text for e in reloaded.read()] == ["a", "b"]
    assert reloaded.last_seq == 2


def test_corrupt_mid_file_line_still_raises(tmp_path):
    import json

    import pytest

    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    log.append(UserMessage(session_id="s1", ts=1.0, text="a"))
    log.append(UserMessage(session_id="s1", ts=2.0, text="b"))
    lines = path.read_text().splitlines()
    lines[0] = "not json"
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(json.JSONDecodeError):
        EventLog(path)
