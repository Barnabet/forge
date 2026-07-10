import pytest
from fastapi.testclient import TestClient

from forge.api.app import create_app
from forge.llm.fake import FakeLLM
from forge.store.config import load_config


@pytest.fixture()
def make_client(tmp_path):
    def _make(script=None):
        config = load_config(tmp_path)
        llm = FakeLLM(script or [])
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
