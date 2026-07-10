import json

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from forge.api.app import create_app
from forge.engine.events import ToolCallSpec
from forge.llm.base import CompletionResult
from forge.llm.fake import FakeLLM
from forge.store.config import ForgeConfig, ModelConfig


def make_client(tmp_path, script, max_concurrent=3):
    cfg = ForgeConfig(models=[ModelConfig(id="m", display_name="m")],
                      default_model="m", max_concurrent=max_concurrent)
    app = create_app(home=tmp_path / "home", config=cfg, llm=FakeLLM(script))
    return TestClient(app)


def drain_until(ws, etype, limit=200):
    for _ in range(limit):
        e = json.loads(ws.receive_text())
        if e["type"] == etype:
            return e
    raise AssertionError(f"never saw {etype}")


def test_full_run_over_ws(tmp_path):
    client = make_client(tmp_path, [
        CompletionResult(text="", tool_calls=[
            ToolCallSpec(id="c1", name="bash", arguments='{"command": "echo hi"}')],
            usage_tokens=1),
        CompletionResult(text="done", usage_tokens=2),
    ])
    with client:
        sid = client.post("/api/sessions", json={"cwd": str(tmp_path)}).json()["id"]
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"cursors": {sid: 0}}))
            r = client.post(f"/api/sessions/{sid}/messages", json={"text": "go"})
            assert r.status_code == 202
            started = drain_until(ws, "tool_call_started")
            assert started["auto_approved"] is True  # yolo default
            drain_until(ws, "run_finished")
        sessions = client.get("/api/sessions").json()
        assert sessions[0]["name"] == "go" and sessions[0]["status"] == "idle"
        events = client.get(f"/api/sessions/{sid}/events").json()
        assert any(e["type"] == "assistant_message" and e["text"] == "done"
                   for e in events)


def test_ws_replay_from_cursor(tmp_path):
    client = make_client(tmp_path, [CompletionResult(text="hi", usage_tokens=1)])
    with client:
        sid = client.post("/api/sessions", json={"cwd": str(tmp_path)}).json()["id"]
        client.post(f"/api/sessions/{sid}/messages", json={"text": "hello"})
        import time
        for _ in range(100):  # wait for run to finish
            if client.get("/api/sessions").json()[0]["status"] == "idle":
                break
            time.sleep(0.05)
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"cursors": {sid: 0}}))
            e = json.loads(ws.receive_text())
            # cursor 0 replays the whole durable log; seq 1 is session_created,
            # which the client needs to build session state.
            assert e["seq"] == 1 and e["type"] == "session_created"  # replayed
            e2 = json.loads(ws.receive_text())
            assert e2["seq"] == 2 and e2["type"] == "user_message"  # replayed


def test_guarded_approval_roundtrip(tmp_path):
    client = make_client(tmp_path, [
        CompletionResult(text="", tool_calls=[
            ToolCallSpec(id="c1", name="bash", arguments='{"command": "echo hi"}')],
            usage_tokens=1),
        CompletionResult(text="done", usage_tokens=2),
    ])
    with client:
        sid = client.post("/api/sessions", json={
            "cwd": str(tmp_path), "autonomy": "guarded"}).json()["id"]
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"cursors": {sid: 0}}))
            client.post(f"/api/sessions/{sid}/messages", json={"text": "go"})
            gate = drain_until(ws, "approval_requested")
            r = client.post(
                f"/api/sessions/{sid}/approvals/{gate['call_id']}",
                json={"decision": "allow"})
            assert r.status_code == 200
            drain_until(ws, "run_finished")


def test_file_search_and_misc_endpoints(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main_app.py").write_text("x")
    # matching files under pruned dirs must never surface
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "main_app.py").write_text("x")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "main_app.py").write_text("x")
    client = make_client(tmp_path, [])
    with client:
        sid = client.post("/api/sessions", json={"cwd": str(tmp_path)}).json()["id"]
        hits = client.get(f"/api/sessions/{sid}/files", params={"q": "mnpy"}).json()
        assert "src/main_app.py" in hits
        assert "node_modules/main_app.py" not in hits
        assert ".git/main_app.py" not in hits
        assert client.get("/api/health").json() == {"ok": True}
        assert client.get("/api/models").json()[0]["id"] == "m"
        assert client.get("/api/skills").json() == []


def test_ws_rejects_cross_origin(tmp_path):
    client = make_client(tmp_path, [])
    with client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                    "/ws", headers={"origin": "https://evil.example"}) as ws:
                ws.receive_text()
