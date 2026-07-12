import asyncio
import json

import pytest

from forge.store.subagent_grades import (
    SubagentGrade, SubagentGradeRecord, SubagentGradeStore,
)


def success_record(model="m1", overall=80, wq=70, info=90, eff=60,
                   session="s1", project="p1", **kw):
    return SubagentGradeRecord(
        status="success",
        grader_model="grader",
        session_id=session,
        project_id=project,
        call_id="c1",
        worker_index=1,
        task="do a thing",
        mode="read",
        subagent_model=model,
        grade=SubagentGrade(
            work_quality=wq, information_delivery=info,
            efficiency=eff, overall=overall,
            rationale="ok", strengths=["a"], issues=["b"]),
        **kw,
    )


def error_record(model="m1", session="s1", **kw):
    return SubagentGradeRecord(
        status="error",
        grader_model="grader",
        session_id=session,
        call_id="c1",
        worker_index=1,
        task="do a thing",
        mode="write",
        subagent_model=model,
        error="grader returned garbage",
        **kw,
    )


def test_score_bounds_validation():
    with pytest.raises(ValueError):
        SubagentGrade(work_quality=101, information_delivery=0,
                      efficiency=0, overall=0)
    with pytest.raises(ValueError):
        SubagentGrade(work_quality=-1, information_delivery=0,
                      efficiency=0, overall=0)


@pytest.mark.asyncio
async def test_append_persists_and_roundtrips(tmp_path):
    store = SubagentGradeStore(tmp_path)
    rec = await store.append(success_record())
    assert (tmp_path / "subagent_grades.jsonl").exists()

    reloaded = SubagentGradeStore(tmp_path)
    got = reloaded.get(rec.id)
    assert got is not None
    assert got.grade.overall == 80
    assert got.status == "success"


@pytest.mark.asyncio
async def test_legacy_record_missing_orchestrator_fields_still_parses(tmp_path):
    rec = success_record()
    payload = rec.model_dump(mode="json")
    payload.pop("orchestrator_model")
    payload.pop("orchestrator_model_inferred")
    (tmp_path / "subagent_grades.jsonl").write_text(json.dumps(payload) + "\n")

    got = SubagentGradeStore(tmp_path).get(rec.id)
    assert got is not None
    assert got.orchestrator_model is None
    assert got.orchestrator_model_inferred is False


@pytest.mark.asyncio
async def test_summaries_carry_orchestrator_provenance(tmp_path):
    store = SubagentGradeStore(tmp_path)
    await store.append(success_record(
        orchestrator_model="parent-model", orchestrator_model_inferred=False))
    (summary,) = store.list()
    assert summary.orchestrator_model == "parent-model"
    assert summary.orchestrator_model_inferred is False


@pytest.mark.asyncio
async def test_get_unknown_returns_none(tmp_path):
    store = SubagentGradeStore(tmp_path)
    await store.append(success_record())
    assert store.get("nope") is None


@pytest.mark.asyncio
async def test_list_recent_first_and_pagination(tmp_path):
    store = SubagentGradeStore(tmp_path)
    await store.append(success_record(overall=10, timestamp=100))
    await store.append(success_record(overall=20, timestamp=200))
    await store.append(success_record(overall=30, timestamp=300))

    summaries = store.list()
    assert [s.overall for s in summaries] == [30, 20, 10]

    page = store.list(limit=1, offset=1)
    assert len(page) == 1 and page[0].overall == 20


@pytest.mark.asyncio
async def test_error_record_summary_has_no_overall(tmp_path):
    store = SubagentGradeStore(tmp_path)
    await store.append(error_record())
    (summary,) = store.list()
    assert summary.status == "error"
    assert summary.overall is None


@pytest.mark.asyncio
async def test_leaderboard_averages_and_ranking(tmp_path):
    store = SubagentGradeStore(tmp_path)
    # model "high": overalls 90 and 70 -> avg 80
    await store.append(success_record(model="high", overall=90, wq=80, info=60, eff=100))
    await store.append(success_record(model="high", overall=70, wq=60, info=40, eff=80))
    # model "low": overall 50 -> avg 50, plus one error (excluded from avg)
    await store.append(success_record(model="low", overall=50, wq=50, info=50, eff=50))
    await store.append(error_record(model="low"))

    board = store.leaderboard()
    assert [e.model for e in board] == ["high", "low"]

    high = board[0]
    assert high.avg_overall == 80.0
    assert high.avg_work_quality == 70.0
    assert high.avg_information_delivery == 50.0
    assert high.avg_efficiency == 90.0
    assert high.sample_count == 2
    assert high.error_count == 0

    low = board[1]
    assert low.avg_overall == 50.0
    assert low.sample_count == 1
    assert low.error_count == 1


@pytest.mark.asyncio
async def test_leaderboard_excludes_low_scoring_successes(tmp_path):
    store = SubagentGradeStore(tmp_path)
    # Eligible sample (overall == threshold 10) and a high one -> avg (10+90)/2 = 50.
    await store.append(success_record(model="m", overall=10, wq=10, info=10, eff=10))
    await store.append(success_record(model="m", overall=90, wq=90, info=90, eff=90))
    # Excluded low-score successes: 0 and 9 contribute to neither sums nor errors.
    await store.append(success_record(model="m", overall=0, wq=100, info=100, eff=100))
    await store.append(success_record(model="m", overall=9, wq=100, info=100, eff=100))

    (entry,) = store.leaderboard()
    assert entry.sample_count == 2
    assert entry.error_count == 0
    assert entry.avg_overall == 50.0
    assert entry.avg_work_quality == 50.0
    assert entry.avg_information_delivery == 50.0
    assert entry.avg_efficiency == 50.0


@pytest.mark.asyncio
async def test_low_scoring_successes_remain_visible_and_not_errors(tmp_path):
    store = SubagentGradeStore(tmp_path)
    low0 = await store.append(success_record(model="m", overall=0))
    low9 = await store.append(success_record(model="m", overall=9))

    # Preserved in list() with their real overall and success status.
    summaries = store.list()
    assert len(summaries) == 2
    assert all(s.status == "success" for s in summaries)
    assert sorted(s.overall for s in summaries) == [0, 9]

    # Preserved and retrievable individually.
    assert store.get(low0.id).grade.overall == 0
    assert store.get(low9.id).grade.overall == 9

    # Not classified as errors on the leaderboard.
    (entry,) = store.leaderboard()
    assert entry.sample_count == 0
    assert entry.error_count == 0


@pytest.mark.asyncio
async def test_leaderboard_tie_break_by_model_id(tmp_path):
    store = SubagentGradeStore(tmp_path)
    await store.append(success_record(model="zeta", overall=50))
    await store.append(success_record(model="alpha", overall=50))
    board = store.leaderboard()
    assert [e.model for e in board] == ["alpha", "zeta"]


@pytest.mark.asyncio
async def test_last_timestamp_tracks_most_recent(tmp_path):
    store = SubagentGradeStore(tmp_path)
    await store.append(success_record(model="m", overall=50, timestamp=100))
    await store.append(error_record(model="m", timestamp=500))
    await store.append(success_record(model="m", overall=60, timestamp=300))
    (entry,) = store.leaderboard()
    assert entry.last_timestamp == 500
    assert entry.sample_count == 2
    assert entry.error_count == 1


@pytest.mark.asyncio
async def test_malformed_lines_are_skipped(tmp_path):
    store = SubagentGradeStore(tmp_path)
    good = await store.append(success_record(overall=42))
    path = tmp_path / "subagent_grades.jsonl"
    with path.open("a") as f:
        f.write("this is not json\n")
        f.write('{"partial": true}\n')  # valid json, invalid schema

    reloaded = SubagentGradeStore(tmp_path)
    summaries = reloaded.list()
    assert len(summaries) == 1
    assert summaries[0].id == good.id
    board = reloaded.leaderboard()
    assert len(board) == 1 and board[0].avg_overall == 42.0


@pytest.mark.asyncio
async def test_concurrent_appends_are_all_persisted(tmp_path):
    store = SubagentGradeStore(tmp_path)
    # Use eligible scores (>= 10) so every append is a leaderboard sample.
    await asyncio.gather(*[
        store.append(success_record(model="m", overall=10 + i))
        for i in range(20)
    ])
    reloaded = SubagentGradeStore(tmp_path)
    assert len(reloaded.list()) == 20
    (entry,) = reloaded.leaderboard()
    assert entry.sample_count == 20


@pytest.mark.asyncio
async def test_orchestrator_scoping_combines_overall_and_separates_filters(tmp_path):
    store = SubagentGradeStore(tmp_path)
    await store.append(success_record(model="worker", overall=90,
                                      orchestrator_model="parent-a"))
    await store.append(success_record(model="worker", overall=30,
                                      orchestrator_model="parent-b"))
    assert store.leaderboard()[0].avg_overall == 60
    assert store.leaderboard("parent-a")[0].avg_overall == 90
    assert store.leaderboard("parent-b")[0].avg_overall == 30


@pytest.mark.asyncio
async def test_orchestrator_scope_preserves_low_grade_and_error_semantics(tmp_path):
    store = SubagentGradeStore(tmp_path)
    await store.append(success_record(overall=9, orchestrator_model="parent-a"))
    await store.append(error_record(orchestrator_model="parent-a"))
    await store.append(success_record(overall=80, orchestrator_model="parent-b"))
    (entry,) = store.leaderboard("parent-a")
    assert entry.sample_count == 0
    assert entry.error_count == 1
    assert entry.avg_overall == 0


@pytest.mark.asyncio
async def test_list_orchestrator_filter_applies_before_pagination(tmp_path):
    store = SubagentGradeStore(tmp_path)
    await store.append(success_record(timestamp=300, orchestrator_model="other"))
    await store.append(success_record(overall=20, timestamp=200,
                                      orchestrator_model="wanted"))
    await store.append(success_record(overall=10, timestamp=100,
                                      orchestrator_model="wanted"))
    page = store.list(limit=1, offset=1, orchestrator_model="wanted")
    assert [row.overall for row in page] == [10]


@pytest.mark.asyncio
async def test_orchestrator_facets_include_unknown_inferred_and_low_scores(tmp_path):
    store = SubagentGradeStore(tmp_path)
    await store.append(success_record(overall=9, timestamp=1,
                                      orchestrator_model="z-model"))
    await store.append(success_record(overall=80, timestamp=2,
                                      orchestrator_model="a-model",
                                      orchestrator_model_inferred=True))
    await store.append(error_record(timestamp=3, orchestrator_model="a-model"))
    legacy = success_record(overall=70, timestamp=4).model_dump(mode="json")
    legacy.pop("orchestrator_model")
    legacy.pop("orchestrator_model_inferred")
    with (tmp_path / "subagent_grades.jsonl").open("a") as f:
        f.write(json.dumps(legacy) + "\n")

    facets = store.orchestrators()
    assert [facet.model for facet in facets] == ["a-model", "z-model", None]
    assert facets[0].model_dump() == {
        "model": "a-model", "record_count": 2, "sample_count": 1,
        "error_count": 1, "last_timestamp": 3.0,
    }
    assert facets[1].record_count == 1 and facets[1].sample_count == 0
    assert facets[1].error_count == 0
    assert facets[2].record_count == 1 and facets[2].sample_count == 1
