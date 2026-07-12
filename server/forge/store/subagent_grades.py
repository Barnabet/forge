from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, ValidationError


class SubagentGrade(BaseModel):
    """Parsed grader scores for one subagent run.

    All four scores are integers on a 0-100 scale. Present only when the
    grader produced a well-formed, parseable grade (record status "success").
    """

    work_quality: int = Field(ge=0, le=100)
    information_delivery: int = Field(ge=0, le=100)
    efficiency: int = Field(ge=0, le=100)
    overall: int = Field(ge=0, le=100)
    rationale: str = ""
    strengths: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)


class SubagentGradeRecord(BaseModel):
    """A complete, self-describing record of one subagent grading event.

    Retained for both successful grades (`status="success"`, `grade` set) and
    grading failures (`status="error"`, `error` set). Everything needed to
    reproduce or audit the grade is captured inline so the record is portable
    across sessions/projects.
    """

    id: str = Field(default_factory=lambda: uuid4().hex)
    timestamp: float = Field(default_factory=time.time)
    status: Literal["success", "error"]

    # Grading provenance
    grader_model: str
    # Exact parent/orchestrator model whose completion emitted the spawn call.
    # Defaults preserve compatibility with historical JSONL records.
    orchestrator_model: str | None = None
    orchestrator_model_inferred: bool = False

    # Run identity / context
    session_id: str
    project_id: str | None = None
    call_id: str          # the subagent tool-call id this worker belongs to
    worker_index: int     # 1-based index within that call
    task: str
    mode: Literal["read", "write"]
    subagent_model: str

    # Performance metadata for the graded run
    turn_count: int = 0
    tool_call_count: int = 0
    usage_tokens: int = 0   # best available total token count for the run
    duration_ms: int = 0

    # Full grader input / context (so grades can be re-derived or audited)
    parent_context: str = ""
    worker_messages: list[dict[str, Any]] = Field(default_factory=list)
    final_report: str = ""

    # Grader output
    raw_grader_response: str = ""
    grade: SubagentGrade | None = None
    error: str | None = None


class RecordSummary(BaseModel):
    """Compact projection of a record for listing views."""

    id: str
    timestamp: float
    status: Literal["success", "error"]
    subagent_model: str
    grader_model: str
    orchestrator_model: str | None = None
    orchestrator_model_inferred: bool = False
    session_id: str
    project_id: str | None = None
    call_id: str
    worker_index: int
    mode: Literal["read", "write"]
    task: str
    overall: int | None = None  # None for error records


class ModelLeaderboardEntry(BaseModel):
    """Aggregated standing for one subagent model."""

    model: str
    avg_overall: float = 0.0
    avg_work_quality: float = 0.0
    avg_information_delivery: float = 0.0
    avg_efficiency: float = 0.0
    sample_count: int = 0   # successful, parsed grades contributing to averages
    error_count: int = 0    # grading failures for this model
    last_timestamp: float = 0.0


class OrchestratorSummary(BaseModel):
    """Aggregate counts for one parent/orchestrator model bucket."""

    model: str | None
    record_count: int = 0
    sample_count: int = 0
    error_count: int = 0
    last_timestamp: float = 0.0


def _summarize(rec: SubagentGradeRecord) -> RecordSummary:
    return RecordSummary(
        id=rec.id,
        timestamp=rec.timestamp,
        status=rec.status,
        subagent_model=rec.subagent_model,
        grader_model=rec.grader_model,
        orchestrator_model=rec.orchestrator_model,
        orchestrator_model_inferred=rec.orchestrator_model_inferred,
        session_id=rec.session_id,
        project_id=rec.project_id,
        call_id=rec.call_id,
        worker_index=rec.worker_index,
        mode=rec.mode,
        task=rec.task,
        overall=rec.grade.overall if rec.grade else None,
    )


class SubagentGradeStore:
    """Append-only JSONL store of subagent grading records.

    Lives globally under Forge home (not per session) because the leaderboard
    ranks models across sessions and projects. Appends are async and guarded
    by an asyncio lock so concurrent worker completions on the same event loop
    serialize their writes. Malformed historical lines are skipped on read so
    one corrupt entry can never brick the leaderboard, but write/append errors
    are never swallowed.
    """

    def __init__(self, home: Path):
        self._path = home / "subagent_grades.jsonl"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    # ---- writes -----------------------------------------------------------

    async def append(self, record: SubagentGradeRecord) -> SubagentGradeRecord:
        line = json.dumps(record.model_dump(mode="json")) + "\n"
        async with self._lock:
            with self._path.open("a") as f:
                f.write(line)
        return record

    # ---- reads ------------------------------------------------------------

    def _iter_records(self) -> list[SubagentGradeRecord]:
        if not self._path.exists():
            return []
        records: list[SubagentGradeRecord] = []
        for line in self._path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                records.append(SubagentGradeRecord.model_validate_json(line))
            except (json.JSONDecodeError, ValidationError):
                continue  # tolerate malformed historical lines
        return records

    def get(self, record_id: str) -> SubagentGradeRecord | None:
        return next((r for r in self._iter_records() if r.id == record_id), None)

    def list(
        self,
        limit: int | None = None,
        offset: int = 0,
        orchestrator_model: str | None = None,
    ) -> list[RecordSummary]:
        """Most-recent-first summaries, filtered before optional pagination."""
        records = self._iter_records()
        if orchestrator_model is not None:
            records = [
                r for r in records if r.orchestrator_model == orchestrator_model
            ]
        records.sort(key=lambda r: r.timestamp, reverse=True)
        window = records[offset:] if limit is None else records[offset:offset + limit]
        return [_summarize(r) for r in window]

    # Successful grades with overall below this threshold are treated as
    # non-representative and excluded from leaderboard averages/sample counts.
    # They are still preserved and visible via list()/get() and are NOT
    # counted as errors.
    _MIN_LEADERBOARD_OVERALL = 10

    def leaderboard(
        self, orchestrator_model: str | None = None,
    ) -> list[ModelLeaderboardEntry]:
        """Per-model aggregation ranked by avg_overall desc, tie-broken by model.

        Only successful parsed grades with ``overall >= 10`` contribute to
        score sums and ``sample_count``. Errors are counted separately in
        ``error_count`` and do not affect averages. Successful grades with
        ``overall < 10`` contribute to neither the averages nor the error
        count; they remain fully visible via list()/get().
        """
        acc: dict[str, dict[str, Any]] = {}
        records = self._iter_records()
        if orchestrator_model is not None:
            records = [
                r for r in records if r.orchestrator_model == orchestrator_model
            ]
        for rec in records:
            entry = acc.setdefault(rec.subagent_model, {
                "work_quality": 0, "information_delivery": 0,
                "efficiency": 0, "overall": 0,
                "samples": 0, "errors": 0, "last": 0.0,
            })
            entry["last"] = max(entry["last"], rec.timestamp)
            if rec.status == "success" and rec.grade is not None:
                if rec.grade.overall < self._MIN_LEADERBOARD_OVERALL:
                    continue  # low-score success: excluded from sums and error_count
                entry["work_quality"] += rec.grade.work_quality
                entry["information_delivery"] += rec.grade.information_delivery
                entry["efficiency"] += rec.grade.efficiency
                entry["overall"] += rec.grade.overall
                entry["samples"] += 1
            else:
                entry["errors"] += 1

        board: list[ModelLeaderboardEntry] = []
        for model, e in acc.items():
            n = e["samples"]
            board.append(ModelLeaderboardEntry(
                model=model,
                avg_overall=e["overall"] / n if n else 0.0,
                avg_work_quality=e["work_quality"] / n if n else 0.0,
                avg_information_delivery=e["information_delivery"] / n if n else 0.0,
                avg_efficiency=e["efficiency"] / n if n else 0.0,
                sample_count=n,
                error_count=e["errors"],
                last_timestamp=e["last"],
            ))
        board.sort(key=lambda x: (-x.avg_overall, x.model))
        return board

    def orchestrators(self) -> list[OrchestratorSummary]:
        """Aggregate records by orchestrator; known model IDs sort before unknown."""
        acc: dict[str | None, dict[str, Any]] = {}
        for rec in self._iter_records():
            entry = acc.setdefault(rec.orchestrator_model, {
                "records": 0, "samples": 0, "errors": 0, "last": 0.0,
            })
            entry["records"] += 1
            entry["last"] = max(entry["last"], rec.timestamp)
            if rec.status == "success" and rec.grade is not None:
                if rec.grade.overall >= self._MIN_LEADERBOARD_OVERALL:
                    entry["samples"] += 1
            else:
                entry["errors"] += 1

        summaries = [
            OrchestratorSummary(
                model=model,
                record_count=e["records"],
                sample_count=e["samples"],
                error_count=e["errors"],
                last_timestamp=e["last"],
            )
            for model, e in acc.items()
        ]
        summaries.sort(key=lambda x: (x.model is None, x.model or ""))
        return summaries
