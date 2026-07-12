from forge.engine.bus import EventBus
from forge.engine.manager import SessionManager
from forge.llm.base import CompletionResult
from forge.llm.fake import FakeLLM
from forge.store.config import ForgeConfig

from tests.test_actor import wait_idle


def make_manager(tmp_path, script=()):
    return SessionManager(home=tmp_path / "home", config=ForgeConfig(),
                          llm=FakeLLM(list(script)), bus=EventBus())


def test_memory_similarity_threshold_wired_into_index(tmp_path):
    cfg = ForgeConfig(openrouter_api_key="sk-test",
                      memory_similarity_threshold=0.77)
    mgr = SessionManager(home=tmp_path / "home", config=cfg,
                         llm=FakeLLM([]), bus=EventBus())
    assert mgr.memory_index is not None
    assert mgr.memory_index.threshold == 0.77


def test_no_memory_index_without_embedder(tmp_path):
    mgr = make_manager(tmp_path)  # default config has no openrouter_api_key
    assert mgr.memory_index is None


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


def test_actors_evicted_past_lru_cap(tmp_path):
    cfg = ForgeConfig(max_resident_sessions=2)
    mgr = SessionManager(home=tmp_path / "home", config=cfg,
                         llm=FakeLLM([]), bus=EventBus())
    a = mgr.create(cwd=str(tmp_path))
    b = mgr.create(cwd=str(tmp_path))
    c = mgr.create(cwd=str(tmp_path))
    # All three known via the meta registry, but only the cap stays resident.
    assert {m.id for m in mgr.list()} == {a.meta.id, b.meta.id, c.meta.id}
    assert len(mgr.actors) == 2
    assert a.meta.id not in mgr.actors  # LRU: first-created evicted


def test_get_faults_evicted_actor_back_in(tmp_path):
    cfg = ForgeConfig(max_resident_sessions=1)
    mgr = SessionManager(home=tmp_path / "home", config=cfg,
                         llm=FakeLLM([]), bus=EventBus())
    a = mgr.create(cwd=str(tmp_path))
    aid = a.meta.id
    mgr.create(cwd=str(tmp_path))  # evicts a
    assert aid not in mgr.actors
    faulted = mgr.get(aid)  # rebuilt from disk
    assert faulted.meta.id == aid
    assert faulted.log.read()[0].type == "session_created"


async def test_busy_actor_not_evicted(tmp_path):
    import asyncio

    cfg = ForgeConfig(max_resident_sessions=1)
    mgr = SessionManager(home=tmp_path / "home", config=cfg,
                         llm=FakeLLM([]), bus=EventBus())
    a = mgr.create(cwd=str(tmp_path))

    async def never():
        await asyncio.Event().wait()

    a.run_task = asyncio.create_task(never())
    try:
        mgr.create(cwd=str(tmp_path))  # would evict a, but a is busy
        assert a.meta.id in mgr.actors
        assert len(mgr.actors) == 2  # cap exceeded because busy actor is pinned
    finally:
        a.run_task.cancel()


async def test_replay_restores_pill_state_from_active_branch(tmp_path):
    mgr = make_manager(tmp_path, [CompletionResult(text="done", usage_tokens=1)])
    a = mgr.create(cwd=str(tmp_path))
    await a.post_message("go")
    await wait_idle(a)
    assert a.meta.unread and a.meta.last_run_reason == "completed"
    run_seq = a.meta.last_run_seq
    assert run_seq is not None

    # A fresh manager rebuilds meta purely from the log; the unread completion,
    # last run reason, and last run seq survive the restart.
    mgr2 = SessionManager(home=tmp_path / "home", config=ForgeConfig(),
                          llm=FakeLLM([]), bus=EventBus())
    mgr2.rehydrate()
    restored = {m.id: m for m in mgr2.list()}[a.meta.id]
    assert restored.unread and restored.last_run_reason == "completed"
    assert restored.last_run_seq == run_seq

    # Acknowledging clears it, and that also survives a subsequent restart.
    mgr2.get(a.meta.id).acknowledge()
    mgr3 = SessionManager(home=tmp_path / "home", config=ForgeConfig(),
                          llm=FakeLLM([]), bus=EventBus())
    mgr3.rehydrate()
    assert not {m.id: m for m in mgr3.list()}[a.meta.id].unread


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
