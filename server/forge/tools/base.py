from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable, Protocol, runtime_checkable

from pydantic import BaseModel

from forge.engine.events import DiffStats, Todo

if TYPE_CHECKING:
    from forge.engine.workspace import SharedWorkspace
    from forge.store.changesets import ChangesetStore
    from forge.store.subagent_grades import SubagentGradeRecord
    from forge.store.workspace_activity import ActivityOrigin


class ToolResult(BaseModel):
    output: str
    is_error: bool = False
    diff_stats: DiffStats | None = None
    todos: list[Todo] | None = None  # update_todos snapshot → durable TodosUpdated
    images: list[str] = []  # base64 data URLs the model should see (e.g. PDF renders)


class TerminalControlError(Exception):
    """Recoverable terminal-control failure (unknown id, not running, cap
    exceeded, bad state). Tools convert this into a ToolResult error rather than
    letting it escape as an uncaught exception."""


@dataclass
class TerminalInfo:
    """Flat, primitive view of one session terminal for tools to format. Keeps
    tools decoupled from the runtime ``Terminal``/``SessionActor`` types."""
    id: str
    command: list[str]
    state: str
    cwd: str
    cols: int
    rows: int
    output_offset: int
    exit_code: int | None = None
    exit_reason: str | None = None


@runtime_checkable
class TerminalController(Protocol):
    """Session-scoped terminal capability handed to tools via ToolContext. The
    actor supplies a concrete adapter; tools never touch SessionActor directly.
    Every method raises TerminalControlError on misuse."""

    async def open(self, argv: list[str], *, cwd: str | None = None,
                   cols: int = 80, rows: int = 24) -> str: ...
    def list(self) -> list[TerminalInfo]: ...
    def info(self, terminal_id: str) -> TerminalInfo: ...
    def read(self, terminal_id: str, after: int = 0) -> tuple[str, int, int, bool]: ...
    def write(self, terminal_id: str, data: str) -> None: ...
    def resize(self, terminal_id: str, cols: int, rows: int) -> None: ...
    def signal(self, terminal_id: str, sig: int) -> None: ...
    def close(self, terminal_id: str) -> None: ...


@dataclass
class ToolContext:
    cwd: Path
    emit_chunk: Callable[[str], None] = field(default=lambda _t: None)
    # Ephemeral progress events (e.g. SubagentUpdate activity lines); kwargs are
    # event fields. Never persisted (seq stays 0).
    emit_event: Callable[..., None] = field(default=lambda **_kw: None)
    # Durable subagent lifecycle snapshots (SubagentState); kwargs are event
    # fields. Persisted to the log so the crew viewer survives reconnect.
    emit_subagent_state: Callable[..., None] = field(default=lambda **_kw: None)
    changesets: "ChangesetStore | None" = None
    # Session terminal capability (PTY-backed). None outside a session actor
    # (e.g. subagents); tools guard on it and report a recoverable error.
    terminals: "TerminalController | None" = None
    # Persist a subagent grading record. Supplied by the actor so tool code
    # never owns the global store directly. None outside a session actor: the
    # grader still runs but its record is dropped (best-effort).
    persist_subagent_grade: "Callable[[SubagentGradeRecord], Awaitable[None]] | None" = None
    # Snapshot of the parent/main-agent turn context up to the spawn_agents call,
    # for grader provenance. Supplied by the actor; empty outside a session actor.
    parent_context: str = ""
    # Exact model used by the parent completion that emitted this tool call.
    orchestrator_model: str | None = None
    # Identifies the spawn_agents tool call (for grade records). "" outside a
    # session actor.
    call_id: str = ""
    # Session that owns this tool call. None outside a session actor.
    session_id: str | None = None
    # Identity used only for read baselines/stale-write detection. Defaults to
    # session_id; concurrent subagent workers set a distinct value so one worker's
    # successful write cannot erase another worker's older observation.
    observation_id: str | None = None
    # Coordinator for the single working tree (shared lock, activity log,
    # content baselines). None outside a session actor; tools guard on it.
    shared_workspace: "SharedWorkspace | None" = None
    # Provenance origin used by controlled file tools. Main-agent calls default to
    # "tool"; delegated workers override this to "subagent".
    activity_origin: "ActivityOrigin" = "tool"
    # Optional action prefix used by delegated workers for worker-level audit
    # detail while session_id/call_id remain the authoritative owner.
    activity_action_prefix: str = ""

    @property
    def baseline_owner(self) -> str | None:
        return self.observation_id if self.observation_id is not None else self.session_id

    def resolve(self, path: str) -> Path:
        p = Path(path)
        return p if p.is_absolute() else (self.cwd / p)


class Tool(ABC):
    name: str
    description: str
    params: dict
    read_only: bool = False
    # When True, the actor must NOT wrap this (mutating) tool in the shared
    # workspace lock. The tool dispatches its own workspace mutations (e.g.
    # spawn_agents coordinates each worker mutation) and is responsible for
    # acquiring the shared lock; wrapping it at dispatch would deadlock workers.
    manages_workspace_lock: bool = False

    def display(self, args: dict) -> str:
        return args.get("path") or args.get("command") or self.name

    def requires_approval(self, args: dict) -> bool:
        return not self.read_only

    @abstractmethod
    async def run(self, args: dict, ctx: ToolContext) -> ToolResult: ...


def openai_spec(tool: Tool) -> dict:
    return {"type": "function", "function": {
        "name": tool.name, "description": tool.description, "parameters": tool.params}}


def truncate_middle(s: str, max_chars: int = 30_000) -> str:
    if len(s) <= max_chars:
        return s
    half = max_chars // 2
    return f"{s[:half]}\n… [{len(s) - max_chars} chars truncated] …\n{s[-half:]}"
