from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, TypeAdapter

Autonomy = Literal["yolo", "guarded"]
Status = Literal["idle", "running", "attention", "queued"]
RunReason = Literal["completed", "cancelled", "interrupted", "error"]
Effort = Literal["default", "low", "medium", "high"]
Mode = Literal["act", "plan"]
TodoStatus = Literal["pending", "in_progress", "completed"]


class Todo(BaseModel):
    text: str
    status: TodoStatus = "pending"


class ToolCallSpec(BaseModel):
    id: str
    name: str
    arguments: str  # raw JSON string, as OpenAI supplies it


class DiffStats(BaseModel):
    path: str
    added: int
    removed: int
    changeset_index: int
    diff: str = ""


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
    images: list[str] = []  # data URLs (base64)
    # True when posted into a live run (steering): the model only sees it on
    # its next completion, so the UI ghosts it until then.
    steering: bool = False
    # Workspace checkpoint id capturing the tree immediately before this message
    # entered history; the rewind target. None on logs predating checkpoints.
    workspace_checkpoint: str | None = None


class MessageCheckpointed(BaseEvent):
    """Attaches a workspace checkpoint to an already-emitted user message. The
    capture runs off the event loop after the bubble is published, so the
    message renders instantly; this durable follow-up records the rewind target
    once the snapshot completes."""
    type: Literal["message_checkpointed"] = "message_checkpointed"
    user_seq: int
    checkpoint: str
    # Workspace activity seq of the checkpoint marker recorded at capture time —
    # the rewind boundary. Records with a higher seq are candidates for rewind
    # conflict inspection. None on logs predating activity-boundary persistence.
    workspace_activity_seq: int | None = None


class AssistantMessage(BaseEvent):
    type: Literal["assistant_message"] = "assistant_message"
    text: str
    tool_calls: list[ToolCallSpec] = []
    usage_tokens: int = 0  # total context size the model reported for this turn
    # Last log seq included in the completion's context: any user message at or
    # below it has been received by the model (un-ghosts steering bubbles).
    context_seq: int = 0


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
    images: list[str] = []  # base64 data URLs the model should see (e.g. PDF renders)


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
    # Whether this completion should mark the session unread until seen. Only
    # successful ``completed`` runs set it. Absent (False) on logs predating
    # this feature, so old completions never retroactively show as unread — the
    # compatibility boundary that anchors "unread" to runs emitted from here on.
    unread: bool = False


class RunAcknowledged(BaseEvent):
    """Durable, idempotent read acknowledgment: records that the latest
    successful ``completed`` run (identified by its ``run_finished`` seq) has
    been seen by the user, clearing the session's unread marker. Emitted only
    when a completed run is currently unread, so replaying the active branch
    yields a monotonic read watermark that rewinds ignore on abandoned
    branches."""
    type: Literal["run_acknowledged"] = "run_acknowledged"
    run_seq: int


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


class ModeChanged(BaseEvent):
    type: Literal["mode_changed"] = "mode_changed"
    mode: Mode


class PlanProposed(BaseEvent):
    type: Literal["plan_proposed"] = "plan_proposed"
    call_id: str
    plan: str


class PlanResolved(BaseEvent):
    type: Literal["plan_resolved"] = "plan_resolved"
    call_id: str
    decision: Literal["approve", "revise"]
    feedback: str = ""


class TodosUpdated(BaseEvent):
    type: Literal["todos_updated"] = "todos_updated"
    todos: list[Todo]


class SubagentState(BaseEvent):
    """Durable snapshot of one spawn_agents worker's lifecycle state, so the
    crew viewer can reconstruct after refresh/reconnect without persisting
    high-frequency activity lines. Only lifecycle transitions land here; the
    live tool-line stream stays ephemeral (SubagentUpdate). Replaying the log
    yields the latest state per worker plus the final report on done/error."""
    type: Literal["subagent_state"] = "subagent_state"
    call_id: str  # parent spawn_agents call
    worker: int  # 1-based index within the spawn
    task: str
    mode: Literal["read", "write"] = "read"
    # "blocked": a write worker holding a concurrency slot but waiting on the
    # shared write lock (another write worker is editing the tree) — distinct
    # from "queued", which is waiting for a slot.
    state: Literal["queued", "running", "blocked", "done", "error"]
    report: str = ""  # final report excerpt on done/error


class TerminalState(BaseEvent):
    """Durable snapshot of one PTY terminal's lifecycle, so the terminal viewer
    can reconstruct its records after refresh/reconnect without persisting the
    raw output stream. Emitted on open, resize, and every final transition
    (exit/close), plus an ``orphaned`` marker written at rehydrate when a replay
    shows a terminal still ``running`` but the runtime process is gone. Replaying
    the log yields the latest state per terminal; the live byte stream stays
    ephemeral (TerminalOutput)."""
    type: Literal["terminal_state"] = "terminal_state"
    terminal_id: str
    command: list[str]
    cwd: str
    cols: int
    rows: int
    # Aligns with the runtime states (starting/running/exited/closed) plus
    # ``orphaned`` for a terminal whose process vanished across a restart.
    state: Literal["starting", "running", "exited", "closed", "orphaned"]
    # Byte cursor one past the newest output produced so far: a fresh reader
    # resumes from here and can detect gaps against TerminalOutput offsets.
    output_offset: int = 0
    exit_code: int | None = None
    exit_reason: str | None = None


class RecalledSnippet(BaseModel):
    tier: Literal["global", "project"]
    region: str
    start_line: int
    end_line: int
    text: str
    score: float


class MemoryRecalled(BaseEvent):
    """Memory snippets retrieved for one user message; rendered below it in
    the projection. Durable so retrieval results never shift between turns."""
    type: Literal["memory_recalled"] = "memory_recalled"
    user_seq: int
    snippets: list[RecalledSnippet]


class HistoryRewound(BaseEvent):
    """Append-only marker that rewinds the active conversation branch to just
    before ``target_user_seq``. Conversational/run events on the then-active
    branch with seq >= target_user_seq become inactive; session settings and
    lifecycle events remain. Never truncates the log."""
    type: Literal["history_rewound"] = "history_rewound"
    target_user_seq: int
    target_checkpoint: str
    safety_checkpoint: str
    # True when a fresh replacement user message follows (edit-and-resend);
    # False for a rewind-only operation.
    replacement: bool


Event = Annotated[
    Union[
        SessionCreated, SessionRenamed, StatusChanged, AutonomyChanged,
        ModelChanged, UserMessage, MessageCheckpointed, AssistantMessage, ToolCallStarted, ToolCallFinished,
        ApprovalRequested, ApprovalResolved, PolicyAdded, ContextCompacted,
        RunFinished, RunAcknowledged, ErrorEvent, SessionArchived, SessionUnarchived, EffortChanged,
        ModeChanged, PlanProposed, PlanResolved, TodosUpdated, MemoryRecalled,
        HistoryRewound, SubagentState, TerminalState,
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


class MemoryUpdate(BaseModel):
    """Progress of the post-run project-memory pass. Never persisted: after a
    reload the indicator simply disappears."""
    seq: int = 0
    session_id: str
    type: Literal["memory_update"] = "memory_update"
    state: Literal["running", "written", "unchanged", "error"]


class FileIndexProgress(BaseModel):
    """Progress of a project's workspace vectorization pass. Project-scoped (no
    session_id) and never persisted: after a reload the current status is
    re-fetched over REST (GET /api/index)."""
    seq: int = 0
    type: Literal["file_index_progress"] = "file_index_progress"
    project_id: str
    state: Literal["indexing", "ready", "error"]
    done: int = 0
    total: int = 0


class CompactionState(BaseModel):
    """Progress of a context-compaction pass (manual or automatic). Never
    persisted: after a reload the indicator simply disappears. `phase` is the
    number of summary sections completed (0..9) and `label` names the current
    section, driving a determinate progress display."""
    seq: int = 0
    session_id: str
    type: Literal["compaction"] = "compaction"
    state: Literal["running", "done"]
    phase: int = 0
    label: str = ""


class SubagentUpdate(BaseModel):
    """Live progress of one spawn_agents worker. Never persisted: the durable
    story is the parent tool_call_finished report."""
    seq: int = 0
    session_id: str
    type: Literal["subagent_update"] = "subagent_update"
    call_id: str  # parent spawn_agents call
    worker: int  # 1-based index within the spawn
    task: str
    mode: Literal["read", "write"] = "read"
    state: Literal["queued", "running", "blocked", "done", "error"]
    activity: str = ""  # latest tool line, e.g. "grep pattern=…"
    report: str = ""  # final report excerpt on done/error


class ToolCallPending(BaseModel):
    """Announced the moment the model starts streaming a tool call, before
    its arguments have finished arriving. The durable ToolCallStarted (or an
    approval gate) supersedes it."""
    seq: int = 0
    session_id: str
    type: Literal["tool_call_pending"] = "tool_call_pending"
    call_id: str
    tool: str


class TerminalOutput(BaseModel):
    """Live decoded output for one PTY terminal. Never persisted: the durable
    story is the TerminalState snapshots plus the terminal's own ring buffer.
    ``start_offset``/``end_offset`` are the monotonic byte cursors bounding this
    chunk, letting the frontend dedupe overlapping replays and detect a gap
    (a ``start_offset`` beyond the last ``end_offset`` it saw)."""
    seq: int = 0
    session_id: str
    type: Literal["terminal_output"] = "terminal_output"
    ts: float
    terminal_id: str
    start_offset: int
    end_offset: int
    text: str


class SteeringConsumed(BaseModel):
    """Announced when a completion STARTS: every pending steering message with
    seq <= context_seq is now part of the context the model is generating
    against, so its bubble can un-ghost (and relocate) immediately instead of
    waiting for the completion to finish. The durable AssistantMessage carries
    the same context_seq and un-ghosts identically on replay."""
    seq: int = 0
    session_id: str
    type: Literal["steering_consumed"] = "steering_consumed"
    context_seq: int
