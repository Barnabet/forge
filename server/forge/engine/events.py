from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, TypeAdapter

Autonomy = Literal["yolo", "guarded"]
Status = Literal["idle", "running", "attention", "queued"]
RunReason = Literal["completed", "cancelled", "interrupted", "error"]
Effort = Literal["default", "low", "medium", "high"]


class ToolCallSpec(BaseModel):
    id: str
    name: str
    arguments: str  # raw JSON string, as OpenAI supplies it


class DiffStats(BaseModel):
    path: str
    added: int
    removed: int
    changeset_index: int


class BaseEvent(BaseModel):
    seq: int = 0  # assigned by EventLog.append; 0 = not yet persisted
    session_id: str
    ts: float


class SessionCreated(BaseEvent):
    type: Literal["session_created"] = "session_created"
    name: str
    cwd: str
    model: str
    autonomy: Autonomy
    project_id: str | None = None
    effort: Effort = "default"


class SessionRenamed(BaseEvent):
    type: Literal["session_renamed"] = "session_renamed"
    name: str


class StatusChanged(BaseEvent):
    type: Literal["status_changed"] = "status_changed"
    status: Status


class AutonomyChanged(BaseEvent):
    type: Literal["autonomy_changed"] = "autonomy_changed"
    autonomy: Autonomy


class ModelChanged(BaseEvent):
    type: Literal["model_changed"] = "model_changed"
    model: str


class UserMessage(BaseEvent):
    type: Literal["user_message"] = "user_message"
    text: str


class AssistantMessage(BaseEvent):
    type: Literal["assistant_message"] = "assistant_message"
    text: str
    tool_calls: list[ToolCallSpec] = []
    usage_tokens: int = 0  # total context size the model reported for this turn


class ToolCallStarted(BaseEvent):
    type: Literal["tool_call_started"] = "tool_call_started"
    call_id: str
    tool: str
    display: str
    auto_approved: bool = False


class ToolCallFinished(BaseEvent):
    type: Literal["tool_call_finished"] = "tool_call_finished"
    call_id: str
    tool: str
    output: str
    is_error: bool = False
    duration_ms: int = 0
    diff_stats: DiffStats | None = None


class ApprovalRequested(BaseEvent):
    type: Literal["approval_requested"] = "approval_requested"
    call_id: str
    tool: str
    display: str


class ApprovalResolved(BaseEvent):
    type: Literal["approval_resolved"] = "approval_resolved"
    call_id: str
    decision: Literal["allow", "deny"]


class PolicyAdded(BaseEvent):
    type: Literal["policy_added"] = "policy_added"
    tool: str
    pattern: str
    scope: Literal["session", "global"]


class ContextCompacted(BaseEvent):
    type: Literal["context_compacted"] = "context_compacted"
    summary: str
    upto_seq: int


class RunFinished(BaseEvent):
    type: Literal["run_finished"] = "run_finished"
    reason: RunReason


class ErrorEvent(BaseEvent):
    type: Literal["error"] = "error"
    message: str


class SessionArchived(BaseEvent):
    type: Literal["session_archived"] = "session_archived"


class SessionUnarchived(BaseEvent):
    type: Literal["session_unarchived"] = "session_unarchived"


class EffortChanged(BaseEvent):
    type: Literal["effort_changed"] = "effort_changed"
    effort: Effort


Event = Annotated[
    Union[
        SessionCreated, SessionRenamed, StatusChanged, AutonomyChanged,
        ModelChanged, UserMessage, AssistantMessage, ToolCallStarted, ToolCallFinished,
        ApprovalRequested, ApprovalResolved, PolicyAdded, ContextCompacted,
        RunFinished, ErrorEvent, SessionArchived, SessionUnarchived, EffortChanged,
    ],
    Field(discriminator="type"),
]

_adapter: TypeAdapter[Event] = TypeAdapter(Event)


def parse_event(d: dict) -> Event:
    return _adapter.validate_python(d)


# Ephemeral (WebSocket-only, never persisted; seq stays 0)
class TextDelta(BaseModel):
    seq: int = 0
    session_id: str
    type: Literal["text_delta"] = "text_delta"
    text: str


class OutputChunk(BaseModel):
    seq: int = 0
    session_id: str
    type: Literal["output_chunk"] = "output_chunk"
    call_id: str
    text: str


class SessionDeleted(BaseModel):
    seq: int = 0
    session_id: str
    type: Literal["session_deleted"] = "session_deleted"
