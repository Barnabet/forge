from starlette.testclient import TestClient

from forge.api.app import create_app
from forge.llm.fake import FakeLLM
from forge.store.config import ForgeConfig, ModelConfig
from forge.store.subagent_grades import SubagentGrade, SubagentGradeRecord


def make_client(tmp_path):
    cfg = ForgeConfig(models=[ModelConfig(id="m", display_name="m")],
                      default_model="m", max_concurrent=3)
    home = tmp_path / "home"
    app = create_app(home=home, config=cfg, llm=FakeLLM([]))
    return TestClient(app), home


def _grade(**kw):
    return SubagentGrade(**{
        "work_quality": 80, "information_delivery": 80,
        "efficiency": 80, "overall": 80, **kw})


def _record(**kw):
    defaults = dict(
        status="success", grader_model="grader",
        session_id="s1", call_id="c1", worker_index=1,
        task="do a thing", mode="read", subagent_model="model-a",
        parent_context="PARENT_CTX", final_report="FINAL_REPORT",
        raw_grader_response="RAW_RESP",
        worker_messages=[{"role": "user", "content": "hi"}],
        grade=_grade(),
    )
    defaults.update(kw)
    return SubagentGradeRecord(**defaults)


def _seed(home, records):
    home.mkdir(parents=True, exist_ok=True)
    path = home / "subagent_grades.jsonl"
    with path.open("a") as f:
        for r in records:
            f.write(r.model_dump_json() + "\n")


def test_empty_store_returns_empty_arrays(tmp_path):
    client, _ = make_client(tmp_path)
    with client:
        assert client.get("/api/subagents/leaderboard").json() == []
        assert client.get("/api/subagents/evaluations").json() == []


def test_leaderboard_aggregation_and_ordering(tmp_path):
    client, home = make_client(tmp_path)
    _seed(home, [
        _record(subagent_model="model-low", grade=_grade(overall=40), timestamp=1.0),
        _record(subagent_model="model-high", grade=_grade(overall=90), timestamp=2.0),
        _record(subagent_model="model-high", grade=_grade(overall=70), timestamp=3.0),
        _record(subagent_model="model-err", status="error", grade=None,
                error="boom", timestamp=4.0),
    ])
    with client:
        board = client.get("/api/subagents/leaderboard").json()
    models = [e["model"] for e in board]
    # avg_overall desc: high (80) > low (40) > err (0)
    assert models == ["model-high", "model-low", "model-err"]
    high = board[0]
    assert high["sample_count"] == 2 and high["avg_overall"] == 80.0
    assert high["last_timestamp"] == 3.0
    err = board[-1]
    assert err["error_count"] == 1 and err["sample_count"] == 0


def test_compact_list_is_lean_and_paginates(tmp_path):
    client, home = make_client(tmp_path)
    _seed(home, [
        _record(task=f"task-{i}", timestamp=float(i)) for i in range(5)
    ])
    with client:
        rows = client.get("/api/subagents/evaluations").json()
        assert len(rows) == 5
        # newest first
        assert rows[0]["task"] == "task-4"
        # no large/full context fields leak
        for row in rows:
            for leaked in ("parent_context", "worker_messages",
                           "final_report", "raw_grader_response"):
                assert leaked not in row
        # pagination
        page = client.get("/api/subagents/evaluations",
                          params={"limit": 2, "offset": 1}).json()
        assert [r["task"] for r in page] == ["task-3", "task-2"]


def test_list_bounds_validation(tmp_path):
    client, _ = make_client(tmp_path)
    with client:
        assert client.get("/api/subagents/evaluations",
                          params={"limit": 0}).status_code == 422
        assert client.get("/api/subagents/evaluations",
                          params={"limit": 201}).status_code == 422
        assert client.get("/api/subagents/evaluations",
                          params={"offset": -1}).status_code == 422


def test_detail_returns_full_context(tmp_path):
    client, home = make_client(tmp_path)
    rec = _record()
    _seed(home, [rec])
    with client:
        body = client.get(f"/api/subagents/evaluations/{rec.id}").json()
    assert body["parent_context"] == "PARENT_CTX"
    assert body["final_report"] == "FINAL_REPORT"
    assert body["raw_grader_response"] == "RAW_RESP"
    assert body["worker_messages"] == [{"role": "user", "content": "hi"}]
    assert body["grade"]["overall"] == 80


def test_unknown_detail_404(tmp_path):
    client, _ = make_client(tmp_path)
    with client:
        assert client.get("/api/subagents/evaluations/nope").status_code == 404


def test_malformed_line_is_tolerated(tmp_path):
    client, home = make_client(tmp_path)
    good = _record(task="good")
    _seed(home, [good])
    with (home / "subagent_grades.jsonl").open("a") as f:
        f.write("{ not valid json\n")
        f.write('{"id": "x", "status": "bogus"}\n')  # schema-invalid
    with client:
        rows = client.get("/api/subagents/evaluations").json()
        assert [r["task"] for r in rows] == ["good"]
        board = client.get("/api/subagents/leaderboard").json()
        assert len(board) == 1


def test_orchestrator_api_filters_and_facets(tmp_path):
    client, home = make_client(tmp_path)
    _seed(home, [
        _record(subagent_model="worker", orchestrator_model="parent-a",
                grade=_grade(overall=90), timestamp=4),
        _record(subagent_model="worker", orchestrator_model="parent-b",
                grade=_grade(overall=30), timestamp=3),
        _record(subagent_model="worker", orchestrator_model="parent-a",
                grade=_grade(overall=9), timestamp=2),
        _record(subagent_model="worker", orchestrator_model="parent-a",
                status="error", grade=None, error="boom", timestamp=1),
        _record(subagent_model="legacy", orchestrator_model=None,
                orchestrator_model_inferred=True, timestamp=5),
    ])
    with client:
        overall = client.get("/api/subagents/leaderboard").json()
        worker = next(entry for entry in overall if entry["model"] == "worker")
        assert worker["avg_overall"] == 60
        assert worker["sample_count"] == 2
        assert worker["error_count"] == 1

        scoped = client.get("/api/subagents/leaderboard",
                            params={"orchestrator_model": "parent-a"}).json()
        assert len(scoped) == 1
        assert scoped[0]["sample_count"] == 1
        assert scoped[0]["error_count"] == 1
        assert scoped[0]["avg_overall"] == 90

        rows = client.get("/api/subagents/evaluations", params={
            "orchestrator_model": "parent-a", "limit": 1, "offset": 1,
        }).json()
        assert len(rows) == 1 and rows[0]["overall"] == 9

        facets = client.get("/api/subagents/orchestrators").json()
        assert [f["model"] for f in facets] == ["parent-a", "parent-b", None]
        parent_a = facets[0]
        assert parent_a["record_count"] == 3
        assert parent_a["sample_count"] == 1
        assert parent_a["error_count"] == 1
        unknown = facets[-1]
        assert unknown["record_count"] == 1 and unknown["sample_count"] == 1


def test_empty_orchestrator_facets(tmp_path):
    client, _ = make_client(tmp_path)
    with client:
        assert client.get("/api/subagents/orchestrators").json() == []
