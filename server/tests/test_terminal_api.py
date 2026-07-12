"""REST terminal route tests. There is no public open endpoint (the agent owns
terminal creation), so we open via the actor on the app's event loop through the
TestClient portal, then exercise list/read/input/resize/signal/close plus the
error contracts (404/409/413/400)."""
import time

import pytest
from fastapi.testclient import TestClient

from forge.api.app import create_app
from forge.llm.fake import FakeLLM
from forge.store.config import load_config


@pytest.fixture()
def client(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr("forge.engine.manager.Path.home", lambda: ws)
    app = create_app(tmp_path, load_config(tmp_path), FakeLLM([]))
    return TestClient(app)


def _open(client, sid, command, cols=80, rows=24):
    actor = client.app.state.manager.get(sid)
    term = client.portal.call(lambda: actor.open_terminal(command, cols=cols, rows=rows))
    return term.id


def _new_session(client, tmp_path):
    return client.post("/api/sessions", json={"cwd": str(tmp_path / "ws")}).json()["id"]


def _wait_read(client, sid, tid, needle, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = client.get(f"/api/sessions/{sid}/terminals/{tid}")
        if needle in r.json()["text"]:
            return r.json()
        time.sleep(0.02)
    raise AssertionError(f"never saw {needle!r}")


def test_list_and_read(client, tmp_path):
    with client:
        sid = _new_session(client, tmp_path)
        tid = _open(client, sid, ["cat"])
        lst = client.get(f"/api/sessions/{sid}/terminals").json()
        assert [t["terminal_id"] for t in lst] == [tid]
        assert lst[0]["command"] == ["cat"]
        r = client.get(f"/api/sessions/{sid}/terminals/{tid}")
        assert r.status_code == 200
        body = r.json()
        assert body["terminal_id"] == tid
        assert body["dropped"] is False and body["start_offset"] == 0


def test_input_echo_and_read_cursor(client, tmp_path):
    with client:
        sid = _new_session(client, tmp_path)
        tid = _open(client, sid, ["cat"])
        r = client.post(f"/api/sessions/{sid}/terminals/{tid}/input",
                        json={"data": "ping\n"})
        assert r.status_code == 200
        body = _wait_read(client, sid, tid, "ping")
        # Resuming from end_offset returns nothing new yet.
        r2 = client.get(f"/api/sessions/{sid}/terminals/{tid}",
                        params={"after": body["end_offset"]})
        assert r2.json()["text"] == ""


def test_resize_route(client, tmp_path):
    with client:
        sid = _new_session(client, tmp_path)
        tid = _open(client, sid, ["cat"])
        r = client.post(f"/api/sessions/{sid}/terminals/{tid}/resize",
                        json={"cols": 120, "rows": 40})
        assert r.status_code == 200


def test_close_route(client, tmp_path):
    with client:
        sid = _new_session(client, tmp_path)
        tid = _open(client, sid, ["cat"])
        r = client.post(f"/api/sessions/{sid}/terminals/{tid}/close")
        assert r.status_code == 200
        st = client.get(f"/api/sessions/{sid}/terminals").json()[0]["state"]
        assert st == "closed"


def test_signal_route_delivers_to_running(client, tmp_path):
    with client:
        sid = _new_session(client, tmp_path)
        tid = _open(client, sid, ["sleep", "60"])
        r = client.post(f"/api/sessions/{sid}/terminals/{tid}/signal",
                        json={"signal": "TERM"})
        assert r.status_code == 200
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if client.get(f"/api/sessions/{sid}/terminals").json()[0]["state"] == "exited":
                break
            time.sleep(0.02)
        assert client.get(f"/api/sessions/{sid}/terminals").json()[0]["state"] == "exited"


# -- error contracts --------------------------------------------------------
def test_unknown_terminal_id_404(client, tmp_path):
    with client:
        sid = _new_session(client, tmp_path)
        assert client.get(f"/api/sessions/{sid}/terminals/nope").status_code == 404
        assert client.post(f"/api/sessions/{sid}/terminals/nope/input",
                           json={"data": "x"}).status_code == 404
        assert client.post(f"/api/sessions/{sid}/terminals/nope/signal",
                           json={"signal": "INT"}).status_code == 404


def test_unknown_session_id_404(client):
    with client:
        assert client.get("/api/sessions/nope/terminals").status_code == 404
        assert client.get("/api/sessions/nope/terminals/x").status_code == 404


def test_cross_session_terminal_404(client, tmp_path):
    with client:
        sid1 = _new_session(client, tmp_path)
        sid2 = _new_session(client, tmp_path)
        tid = _open(client, sid1, ["cat"])
        # A terminal from another session is unknown here.
        assert client.get(f"/api/sessions/{sid2}/terminals/{tid}").status_code == 404


def test_oversized_input_413(client, tmp_path):
    with client:
        sid = _new_session(client, tmp_path)
        tid = _open(client, sid, ["cat"])
        r = client.post(f"/api/sessions/{sid}/terminals/{tid}/input",
                        json={"data": "x" * (64 * 1024 + 1)})
        assert r.status_code == 413


def test_invalid_dimensions_400(client, tmp_path):
    with client:
        sid = _new_session(client, tmp_path)
        tid = _open(client, sid, ["cat"])
        for dims in ({"cols": 0, "rows": 24}, {"cols": 80, "rows": 99999}):
            r = client.post(f"/api/sessions/{sid}/terminals/{tid}/resize", json=dims)
            assert r.status_code == 400


def test_invalid_signal_400(client, tmp_path):
    with client:
        sid = _new_session(client, tmp_path)
        tid = _open(client, sid, ["cat"])
        r = client.post(f"/api/sessions/{sid}/terminals/{tid}/signal",
                        json={"signal": "HUP"})
        assert r.status_code == 400


def test_operations_on_exited_terminal_409(client, tmp_path):
    with client:
        sid = _new_session(client, tmp_path)
        tid = _open(client, sid, ["python3", "-c", "print('x')"])
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if client.get(f"/api/sessions/{sid}/terminals").json()[0]["state"] == "exited":
                break
            time.sleep(0.02)
        assert client.get(f"/api/sessions/{sid}/terminals").json()[0]["state"] == "exited"
        assert client.post(f"/api/sessions/{sid}/terminals/{tid}/input",
                           json={"data": "y"}).status_code == 409
        assert client.post(f"/api/sessions/{sid}/terminals/{tid}/resize",
                           json={"cols": 100, "rows": 30}).status_code == 409
        # Signalling a reaped terminal must 409, never killpg a recycled pgid.
        assert client.post(f"/api/sessions/{sid}/terminals/{tid}/signal",
                           json={"signal": "INT"}).status_code == 409
