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
