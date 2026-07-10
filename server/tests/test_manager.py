from forge.engine.bus import EventBus
from forge.engine.manager import SessionManager
from forge.llm.base import CompletionResult
from forge.llm.fake import FakeLLM
from forge.store.config import ForgeConfig

from tests.test_actor import wait_idle


def make_manager(tmp_path, script=()):
    return SessionManager(home=tmp_path / "home", config=ForgeConfig(),
                          llm=FakeLLM(list(script)), bus=EventBus())


def test_create_defaults_cascade(tmp_path):
    mgr = make_manager(tmp_path)
    a = mgr.create(cwd=str(tmp_path))
    b = mgr.create()  # inherits previous session's cwd
    assert b.meta.cwd == str(tmp_path)
    assert a.meta.autonomy == "yolo" and a.meta.model == ForgeConfig().default_model
    assert {m.id for m in mgr.list()} == {a.meta.id, b.meta.id}
    assert a.log.read()[0].type == "session_created"


async def test_rehydrate_restores_and_marks_interrupted(tmp_path):
    mgr = make_manager(tmp_path, [CompletionResult(text="ok", usage_tokens=1)])
    a = mgr.create(cwd=str(tmp_path))
    await a.post_message("do the thing")
    await wait_idle(a)
    # simulate a crash mid-run on a second session: log ends right after a
    # user_message with no run_finished (create() already emitted session_created)
    from forge.engine.events import UserMessage

    b = mgr.create()
    b.emit(UserMessage(session_id=b.meta.id, ts=0.0, text="crashed mid-run"))

    mgr2 = SessionManager(home=tmp_path / "home", config=ForgeConfig(),
                          llm=FakeLLM([]), bus=EventBus())
    mgr2.rehydrate()
    metas = {m.id: m for m in mgr2.list()}
    assert metas[a.meta.id].name == "do the thing"
    assert metas[a.meta.id].status == "idle"
    evs = mgr2.get(b.meta.id).log.read()
    assert evs[-1].type == "run_finished" and evs[-1].reason == "interrupted"


def test_rehydrate_skips_corrupt_session_dir(tmp_path):
    mgr = make_manager(tmp_path)
    a = mgr.create(cwd=str(tmp_path))
    b = mgr.create()
    # corrupt session b's log mid-file (a torn *trailing* line is handled by EventLog)
    log_path = tmp_path / "home" / "sessions" / b.meta.id / "events.jsonl"
    log_path.write_text("garbage not json\n" + log_path.read_text())

    mgr2 = SessionManager(home=tmp_path / "home", config=ForgeConfig(),
                          llm=FakeLLM([]), bus=EventBus())
    mgr2.rehydrate()
    ids = {m.id for m in mgr2.list()}
    assert a.meta.id in ids and b.meta.id not in ids
