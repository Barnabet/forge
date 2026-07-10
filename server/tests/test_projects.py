import json

import pytest
from fastapi.testclient import TestClient

from forge.api.app import create_app
from forge.llm.fake import FakeLLM
from forge.store.config import load_config


@pytest.fixture()
def client(tmp_path):
    app = create_app(tmp_path, load_config(tmp_path), FakeLLM([]))
    with TestClient(app) as c:
        yield c


def test_project_crud_roundtrip(client, tmp_path):
    work = tmp_path / "proj"
    work.mkdir()
    p = client.post("/api/projects", json={
        "name": "mygent", "cwd": str(work), "default_model": "gpt-5.2",
    }).json()
    assert p["name"] == "mygent" and p["default_model"] == "gpt-5.2"
    assert p["default_autonomy"] == "" and p["default_effort"] == ""

    assert client.get("/api/projects").json() == [p]

    p2 = client.patch(f"/api/projects/{p['id']}", json={"name": "renamed"}).json()
    assert p2["name"] == "renamed" and p2["cwd"] == str(work)

    assert client.delete(f"/api/projects/{p['id']}").status_code == 200
    assert client.get("/api/projects").json() == []


def test_project_persistence_across_restarts(tmp_path):
    work = tmp_path / "proj"
    work.mkdir()
    app = create_app(tmp_path, load_config(tmp_path), FakeLLM([]))
    with TestClient(app) as c:
        pid = c.post("/api/projects",
                     json={"name": "n", "cwd": str(work)}).json()["id"]
    raw = json.loads((tmp_path / "projects.json").read_text())
    assert raw[0]["id"] == pid
    app2 = create_app(tmp_path, load_config(tmp_path), FakeLLM([]))
    with TestClient(app2) as c:
        assert c.get("/api/projects").json()[0]["id"] == pid


def test_project_validation(client, tmp_path):
    r = client.post("/api/projects", json={"name": "x", "cwd": str(tmp_path / "nope")})
    assert r.status_code == 400
    assert client.patch("/api/projects/zzzz", json={"name": "x"}).status_code == 404
    assert client.delete("/api/projects/zzzz").status_code == 404


def test_recent_dirs_distinct_most_recent_first(client, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    client.post("/api/sessions", json={"cwd": str(a)})
    client.post("/api/sessions", json={"cwd": str(b)})
    client.post("/api/sessions", json={"cwd": str(a)})  # duplicate, newest
    assert client.get("/api/recent_dirs").json() == [str(a), str(b)]
