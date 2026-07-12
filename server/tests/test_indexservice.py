from forge.engine.bus import EventBus
from forge.engine.fileindex import FileIndex
from forge.engine.indexservice import IndexService
from forge.llm.embeddings import FakeEmbedder


class _Proj:
    def __init__(self, pid: str, cwd: str):
        self.id = pid
        self.cwd = cwd


def _drain(bus, q):
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    return events


async def test_index_project_transitions_to_ready(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "a.txt").write_text("alpha content here")
    (root / "b.txt").write_text("beta content there")
    home = tmp_path / "home"
    bus = EventBus()
    q = bus.subscribe()
    svc = IndexService(bus, FileIndex(home, FakeEmbedder()),
                       max_bytes=262144, max_files=5000)
    await svc.index_project("proj", str(root))
    assert svc.status["proj"]["state"] == "ready"
    assert svc.status["proj"]["total"] >= 1
    states = [e.state for e in _drain(bus, q) if e.type == "file_index_progress"]
    assert states[0] == "indexing" and states[-1] == "ready"


async def test_index_project_none_file_index_is_noop(tmp_path):
    bus = EventBus()
    svc = IndexService(bus, None, max_bytes=262144, max_files=5000)
    await svc.index_project("proj", str(tmp_path))
    assert svc.status == {}


async def test_index_project_error_sets_error_state(tmp_path):
    class BoomEmbedder(FakeEmbedder):
        async def embed(self, texts):
            raise RuntimeError("boom")

    root = tmp_path / "proj"
    root.mkdir()
    (root / "a.txt").write_text("content that must embed")
    bus = EventBus()
    svc = IndexService(bus, FileIndex(tmp_path / "home", BoomEmbedder()),
                       max_bytes=262144, max_files=5000)
    await svc.index_project("proj", str(root))
    assert svc.status["proj"]["state"] == "error"


async def test_index_all_schedules_each_project(tmp_path):
    import asyncio

    root = tmp_path / "proj"
    root.mkdir()
    (root / "a.txt").write_text("some content")
    bus = EventBus()
    svc = IndexService(bus, FileIndex(tmp_path / "home", FakeEmbedder()),
                       max_bytes=262144, max_files=5000)
    svc.index_all([_Proj("proj", str(root))])
    assert "proj" in svc._tasks
    while svc._tasks:  # let the scheduled background task finish
        await asyncio.sleep(0)
    assert svc.status["proj"]["state"] == "ready"
