import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from forge.api.app import create_app
from forge.llm.base import CompletionResult
from forge.llm.fake import FakeLLM
from forge.store.config import load_config


@pytest.fixture()
def make_client(tmp_path):
    def _make(script=None, delay=0.0):
        config = load_config(tmp_path)
        llm = FakeLLM(script or [], delay=delay)
        app = create_app(tmp_path, config, llm)
        return TestClient(app)
    return _make


def test_set_model_emits_event_and_updates_meta(make_client):
    client = make_client()
    with client:
        sid = client.post("/api/sessions", json={}).json()["id"]
        r = client.post(f"/api/sessions/{sid}/model", json={"model": "gpt-5.2"})
        assert r.status_code == 200
        metas = client.get("/api/sessions").json()
        assert metas[0]["model"] == "gpt-5.2"
        events = client.get(f"/api/sessions/{sid}/events").json()
        assert events[-1]["type"] == "model_changed"
        assert events[-1]["model"] == "gpt-5.2"


def test_set_model_rejects_unknown_id(make_client):
    client = make_client()
    with client:
        sid = client.post("/api/sessions", json={}).json()["id"]
        r = client.post(f"/api/sessions/{sid}/model", json={"model": "nope"})
        assert r.status_code == 400


def test_model_change_survives_rehydrate(tmp_path):
    config = load_config(tmp_path)
    app = create_app(tmp_path, config, FakeLLM([]))
    with TestClient(app) as client:
        sid = client.post("/api/sessions", json={}).json()["id"]
        client.post(f"/api/sessions/{sid}/model", json={"model": "gpt-5.2"})
    app2 = create_app(tmp_path, load_config(tmp_path), FakeLLM([]))
    with TestClient(app2) as client:
        assert client.get("/api/sessions").json()[0]["model"] == "gpt-5.2"


def test_manual_compact_emits_event(make_client):
    client = make_client(script=[
        CompletionResult(text="hi there", tool_calls=[], usage_tokens=10),
        CompletionResult(text="SUMMARY", tool_calls=[], usage_tokens=5),
    ])
    with client:
        sid = client.post("/api/sessions", json={}).json()["id"]
        client.post(f"/api/sessions/{sid}/messages", json={"text": "hello"})
        # wait for the run to finish (idle status in meta)
        for _ in range(100):
            if client.get("/api/sessions").json()[0]["status"] == "idle":
                break
        r = client.post(f"/api/sessions/{sid}/compact")
        assert r.status_code == 200
        events = client.get(f"/api/sessions/{sid}/events").json()
        compacted = [e for e in events if e["type"] == "context_compacted"]
        assert compacted and compacted[-1]["summary"] == "SUMMARY"
        assert compacted[-1]["upto_seq"] > 0


def test_compact_while_running_is_409(make_client):
    # a FakeLLM with a delay keeps the run active while we hit /compact
    client = make_client(script=[
        CompletionResult(text="slow", tool_calls=[], usage_tokens=10),
    ], delay=0.3)
    with client:
        sid = client.post("/api/sessions", json={}).json()["id"]
        client.post(f"/api/sessions/{sid}/messages", json={"text": "go"})
        r = client.post(f"/api/sessions/{sid}/compact")
        assert r.status_code == 409


def test_unknown_session_is_404(make_client):
    client = make_client()
    with client:
        assert client.get("/api/sessions/nope/events").status_code == 404
        assert client.post("/api/sessions/nope/messages",
                           json={"text": "x"}).status_code == 404
        assert client.post("/api/sessions/nope/cancel").status_code == 404


def test_changeset_file_content(make_client, tmp_path):
    client = make_client()
    with client:
        sid = client.post("/api/sessions",
                          json={"cwd": str(tmp_path)}).json()["id"]
        actor = client.app.state.manager.get(sid)
        target = tmp_path / "hello.txt"
        actor.changesets.record(target, None, "new content\n")
        r = client.get(f"/api/sessions/{sid}/changesets/0/file")
        assert r.status_code == 200
        assert r.json() == {"path": str(target), "content": "new content\n"}
        assert client.get(
            f"/api/sessions/{sid}/changesets/9/file").status_code == 404


def test_ws_malformed_first_payload_closes_4400(make_client):
    client = make_client()
    with client:
        with pytest.raises(WebSocketDisconnect) as excinfo:
            with client.websocket_connect("/ws") as ws:
                ws.send_text("this is not json")
                ws.receive_text()
        assert excinfo.value.code == 4400


def test_create_session_resolves_project_defaults(make_client, tmp_path):
    client = make_client()
    with client:
        work = tmp_path / "proj"
        work.mkdir()
        pid = client.post("/api/projects", json={
            "name": "p", "cwd": str(work),
            "default_model": "gpt-5.2", "default_autonomy": "guarded",
            "default_effort": "high",
        }).json()["id"]
        meta = client.post("/api/sessions", json={"project_id": pid}).json()
        assert meta["cwd"] == str(work)
        assert meta["model"] == "gpt-5.2"
        assert meta["autonomy"] == "guarded"
        assert meta["effort"] == "high"
        assert meta["project_id"] == pid
        assert meta["archived"] is False
        # explicit beats project default
        meta2 = client.post("/api/sessions", json={
            "project_id": pid, "effort": "low"}).json()
        assert meta2["effort"] == "low" and meta2["model"] == "gpt-5.2"
        # unknown project
        assert client.post("/api/sessions",
                           json={"project_id": "zzzz"}).status_code == 400


def test_project_fields_survive_rehydrate(tmp_path):
    config = load_config(tmp_path)
    with TestClient(create_app(tmp_path, config, FakeLLM([]))) as client:
        work = tmp_path / "proj"
        work.mkdir()
        pid = client.post("/api/projects", json={
            "name": "p", "cwd": str(work), "default_effort": "high"}).json()["id"]
        sid = client.post("/api/sessions", json={"project_id": pid}).json()["id"]
    with TestClient(create_app(tmp_path, load_config(tmp_path), FakeLLM([]))) as client:
        metas = {m["id"]: m for m in client.get("/api/sessions").json()}
        assert metas[sid]["project_id"] == pid
        assert metas[sid]["effort"] == "high"


def test_old_logs_without_new_fields_still_parse(tmp_path):
    # V1 logs have session_created without project_id/effort — must default
    from forge.engine.events import parse_event
    e = parse_event({"type": "session_created", "seq": 1, "session_id": "x",
                     "ts": 0, "name": "n", "cwd": "/w", "model": "m",
                     "autonomy": "yolo"})
    assert e.project_id is None and e.effort == "default"
