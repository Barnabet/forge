from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from pathlib import Path
from typing import Callable

from pydantic import BaseModel

from forge.engine.bus import EventBus
from forge.engine.fileindex import FileIndex
from forge.engine.memindex import MemoryIndex
from forge.engine.memory import MemoryAgent
from forge.engine.events import (
    ApprovalRequested, ApprovalResolved, AssistantMessage, Autonomy,
    AutonomyChanged, CompactionState, ContextCompacted, Effort, EffortChanged, ErrorEvent,
    HistoryRewound, MessageCheckpointed, Mode, MemoryRecalled, MemoryUpdate, ModeChanged,
    ModelChanged, OutputChunk, PlanProposed, PlanResolved, PolicyAdded,
    RecalledSnippet, RunAcknowledged, RunFinished, RunReason, SessionArchived,
    SessionRenamed,
    SessionUnarchived, Status, StatusChanged, SteeringConsumed, SubagentState,
    SubagentUpdate, TerminalOutput, TerminalState, TextDelta,
    TodosUpdated, ToolCallFinished, ToolCallPending, ToolCallSpec,
    ToolCallStarted, UserMessage,
)
from forge.engine.projection import (
    active_events, active_user_seqs, dangling_call_ids, latest_run,
    loaded_skill_names, message_activity_boundaries, message_checkpoints,
    to_messages, unread_run_seq,
)
from forge.engine.scheduler import Scheduler
from forge.engine.skills import skill_tool_activations, stock_skills_dir
from forge.engine.terminal import SessionTerminals, Terminal, TerminalError
from forge.engine.workspace import SharedWorkspace, WorkspaceRegistry
from forge.llm.base import LLMClient, LLMError
from forge.store.changesets import ChangesetStore
from forge.store.config import ForgeConfig, Policy, policy_matches, save_global_policy
from forge.store.eventlog import EventLog
from forge.store.rewind_intent import RewindIntent, RewindIntentStore
from forge.store.subagent_grades import SubagentGradeRecord, SubagentGradeStore
from forge.store.workspace_checkpoints import (
    WorkspaceCheckpointError, WorkspaceCheckpointStore,
)
from forge.tools.base import (
    TerminalControlError,
    TerminalInfo,
    Tool,
    ToolContext,
    openai_spec,
)
from forge.tools.bash import BashTool
from forge.tools.file_search import SearchFilesTool
from forge.tools.memory_tools import ReadMemoryTool, RememberTool
from forge.tools.plan import PLAN_TOOL_NAME
from forge.tools.registry import (
    default_tools, image_tool_from_config, web_tools_from_config,
)
from forge.tools.skills_tool import LoadSkillTool
from forge.tools.subagents import SpawnAgentsTool

COMPACT_THRESHOLD = 0.75

# One SubagentGradeStore per Forge home, shared across every session actor so
# concurrent worker completions append to the same global JSONL (the store's
# own asyncio lock serializes writes on the event loop). The leaderboard ranks
# models across sessions/projects, so the store must not be per-session.
_GRADE_STORES: dict[Path, SubagentGradeStore] = {}


def _grade_store(home: Path) -> SubagentGradeStore:
    store = _GRADE_STORES.get(home)
    if store is None:
        store = SubagentGradeStore(home)
        _GRADE_STORES[home] = store
    return store


# Fallback WorkspaceRegistry per Forge home, used only when an actor is built
# directly (e.g. tests) without a manager-injected SharedWorkspace. Keyed by
# home so two actors constructed under the same home + same resolved cwd still
# share one lock, matching the manager-injected path.
_DEFAULT_REGISTRIES: dict[Path, WorkspaceRegistry] = {}


def _default_registry(home: Path) -> WorkspaceRegistry:
    reg = _DEFAULT_REGISTRIES.get(home)
    if reg is None:
        reg = WorkspaceRegistry(home)
        _DEFAULT_REGISTRIES[home] = reg
    return reg

# The compaction summary is a fixed, ordered set of numbered sections. The
# server watches the streamed output for each header crossing to drive a
# determinate progress display (phase N of len(COMPACT_SECTIONS)).
COMPACT_SECTIONS = [
    "Primary Request and Intent",
    "Key Technical Concepts",
    "Files and Code Sections",
    "Errors and fixes",
    "Problem Solving",
    "All user messages",
    "Pending Tasks",
    "Current Work",
    "Optional Next Step",
]

COMPACT_PROMPT = """\
Your task is to create a detailed summary of the conversation so far, paying \
close attention to the user's explicit requests and your previous actions.
This summary should be thorough in capturing technical details, code patterns, \
and architectural decisions that would be essential for continuing development \
work without losing context.

Before providing your final summary, wrap your analysis in <analysis> tags to \
organize your thoughts and ensure you've covered all necessary points. In your \
analysis process:

1. Chronologically analyze each message and section of the conversation. For \
each section thoroughly identify:
   - The user's explicit requests and intents
   - Your approach to addressing the user's requests
   - Key decisions, technical concepts and code patterns
   - Specific details like file names, full code snippets, function signatures, \
and file edits
   - Errors that you ran into and how you fixed them
   - Pay special attention to specific user feedback that you received, \
especially if the user told you to do something differently.
2. Double-check for technical accuracy and completeness, addressing each \
required element thoroughly.

Your summary should include the following sections, each on its own line \
starting with its number and title exactly as written:

1. Primary Request and Intent: Capture all of the user's explicit requests and \
intents in detail.
2. Key Technical Concepts: List all important technical concepts, technologies, \
and frameworks discussed.
3. Files and Code Sections: Enumerate specific files and code sections examined, \
modified, or created. Pay special attention to the most recent messages and \
include full code snippets where applicable and a summary of why each file read \
or edit is important.
4. Errors and fixes: List all errors that you ran into, and how you fixed them. \
Pay special attention to specific user feedback, especially if the user told you \
to do something differently.
5. Problem Solving: Document problems solved and any ongoing troubleshooting \
efforts.
6. All user messages: List ALL user messages that are not tool results. These \
are critical for understanding the user's feedback and changing intent.
7. Pending Tasks: Outline any pending tasks that you have explicitly been asked \
to work on, including the approved plan if any and the current todo list with \
each item's status.
8. Current Work: Describe in detail precisely what was being worked on \
immediately before this summary request, paying special attention to the most \
recent messages. Include file names and code snippets where applicable.
9. Optional Next Step: List the next step that you will take that is related to \
the most recent work you were doing. IMPORTANT: ensure that this step is \
DIRECTLY in line with the user's most recent explicit requests. If your last \
task was concluded, only list next steps if they are explicitly in line with \
the user's request. Include direct quotes from the most recent conversation \
showing exactly where you left off.

Structure your output as an <analysis> block followed by a <summary> block \
containing the nine numbered sections."""

logger = logging.getLogger(__name__)


def _summary_body(text: str) -> str:
    """Keep only the <summary> block, dropping the model's <analysis> reasoning
    pass. Falls back to the full text if the tags are absent."""
    start = text.find("<summary>")
    end = text.find("</summary>")
    if start != -1 and end != -1 and end > start:
        return text[start + len("<summary>"):end].strip()
    return text.strip()


class RewindError(Exception):
    """Base class for rewind failures the API maps to precise HTTP codes."""


class RewindTargetMissing(RewindError):
    """The requested target seq is not a user message in the log (→ 404)."""


class RewindTargetInactive(RewindError):
    """The target user message is not on the active branch (→ 409)."""


class RewindNoCheckpoint(RewindError):
    """The target predates checkpoints and has no restore point (→ 409)."""


class RewindEmptyReplacement(RewindError):
    """Edit-and-resend supplied empty text and no images (→ 400)."""


class RewindWorkspaceError(RewindError):
    """The workspace checkpoint could not be captured or restored (→ 409)."""


class RewindConflict(RewindError):
    """A restore-touched path was changed after the target checkpoint by another
    session or an external process, so rewinding would clobber unrelated work
    (→ 409). Carries the conflicting paths and their authors."""

    def __init__(self, conflicts: list[tuple[str, str]]):
        self.conflicts = conflicts
        detail = "; ".join(f"{p} (last changed by {a})" for p, a in conflicts)
        super().__init__(
            "cannot rewind: the following path(s) were changed after the target "
            f"checkpoint by another author: {detail}")


class RewindProvenanceUnavailable(RewindError):
    """The set of paths a restore would touch could not be reliably computed
    (target checkpoint tree missing, or the session checkpoint store could not
    snapshot/diff the live tree), so we cannot prove the rewind is safe. We fail
    closed rather than restore blind and risk clobbering unrelated work (→ 409)."""


class TerminalNotFound(Exception):
    """No terminal with the given id in this session (→ 404)."""


class TerminalNotRunning(Exception):
    """A running-only operation (write/resize) hit a non-running terminal (→ 409)."""


class ActorTerminalController:
    """Adapts a SessionActor's terminal surface to the tools' TerminalController
    protocol. Translates the actor's TerminalNotFound/TerminalNotRunning and the
    runtime's TerminalError into a single recoverable TerminalControlError so
    tools never see actor/runtime internals or uncaught exceptions."""

    def __init__(self, actor: "SessionActor"):
        self._actor = actor

    async def open(self, argv: list[str], *, cwd: str | None = None,
                   cols: int = 80, rows: int = 24) -> str:
        try:
            term = await self._actor.open_terminal(argv, cwd=cwd, cols=cols, rows=rows)
        except TerminalError as e:
            raise TerminalControlError(str(e)) from None
        return term.id

    def list(self) -> list[TerminalInfo]:
        return [self._info(t) for t in self._actor.list_terminals()]

    def info(self, terminal_id: str) -> TerminalInfo:
        try:
            return self._info(self._actor._get_terminal(terminal_id))
        except TerminalNotFound as e:
            raise TerminalControlError(f"unknown terminal: {e}") from None

    def read(self, terminal_id: str, after: int = 0) -> tuple[str, int, int, bool]:
        try:
            return self._actor.read_terminal(terminal_id, after)
        except TerminalNotFound:
            raise TerminalControlError(f"unknown terminal: {terminal_id}") from None

    def write(self, terminal_id: str, data: str) -> None:
        self._guarded(lambda: self._actor.write_terminal(terminal_id, data), terminal_id)

    def resize(self, terminal_id: str, cols: int, rows: int) -> None:
        self._guarded(
            lambda: self._actor.resize_terminal(terminal_id, cols, rows), terminal_id)

    def signal(self, terminal_id: str, sig: int) -> None:
        self._guarded(
            lambda: self._actor.signal_terminal(terminal_id, sig), terminal_id)

    def close(self, terminal_id: str) -> None:
        self._guarded(lambda: self._actor.close_terminal(terminal_id), terminal_id)

    def _guarded(self, fn, terminal_id: str) -> None:
        try:
            fn()
        except TerminalNotFound:
            raise TerminalControlError(f"unknown terminal: {terminal_id}") from None
        except TerminalNotRunning as e:
            raise TerminalControlError(str(e)) from None

    @staticmethod
    def _info(term: Terminal) -> TerminalInfo:
        return TerminalInfo(
            id=term.id, command=list(term.command), state=term.state,
            cwd=term.cwd, cols=term.cols, rows=term.rows,
            output_offset=term.buffer.end, exit_code=term.exit_code,
            exit_reason=term.exit_reason)


class SessionMeta(BaseModel):
    id: str
    name: str = "New session"
    cwd: str
    model: str
    autonomy: Autonomy = "yolo"
    status: Status = "idle"
    project_id: str | None = None
    archived: bool = False
    effort: Effort = "default"
    mode: Mode = "act"
    last_message_at: float | None = None
    # Reason of the latest run_finished on the active branch (drives the session
    # pill state), or None before any run finishes. Rewinds re-derive it from
    # the active branch, so an abandoned branch's outcome never lingers.
    last_run_reason: RunReason | None = None
    # Seq of that same latest run_finished on the active branch, or None before
    # any run finishes. Lets clients correlate the cached pill state with the
    # concrete run event and avoid protocol/store races. Re-derived alongside
    # last_run_reason so rewinds/acks keep the two in sync.
    last_run_seq: int | None = None
    # True when the latest completed run has not yet been acknowledged as read.
    # Only successful completions emitted after this feature can set it; see
    # projection.unread_run_seq for the compatibility boundary.
    unread: bool = False


class SessionActor:
    def __init__(self, meta: SessionMeta, home: Path, config: ForgeConfig,
                 llm: LLMClient, bus: EventBus, scheduler: Scheduler,
                 system_prompt_fn: Callable[[SessionMeta], str],
                 memory_agent: MemoryAgent | None = None,
                 memory_index: MemoryIndex | None = None,
                 file_index: FileIndex | None = None,
                 shared_workspace: SharedWorkspace | None = None):
        self.meta = meta
        self.home = home
        self.config = config
        self.llm = llm
        self.bus = bus
        self.scheduler = scheduler
        self.system_prompt_fn = system_prompt_fn
        self.memory_agent = memory_agent
        self.memory_index = memory_index
        sdir = home / "sessions" / meta.id
        self.log = EventLog(sdir / "events.jsonl")
        self.changesets = ChangesetStore(sdir)
        self.checkpoints = WorkspaceCheckpointStore(
            sdir / "workspace.git", Path(meta.cwd))
        self.rewind_intent = RewindIntentStore(sdir / "rewind-intent.json")
        # Every session whose cwd resolves to the same real directory shares one
        # SharedWorkspace, so its lock, activity log, and content baselines are
        # coordinated across sessions on the single working tree. When no
        # workspace is injected (direct construction, e.g. tests), fall back to a
        # per-home registry so equivalent cwds still share one object.
        self.shared_workspace = shared_workspace or _default_registry(
            home).get(meta.cwd)
        # Serializes Forge-controlled workspace mutations (non-read-only tools,
        # changeset revert, /fs writes) against checkpoint capture/restore so a
        # snapshot never races a partially-applied mutation. Aliased to the
        # shared workspace lock so all sessions on this tree serialize together.
        self.workspace_lock = self.shared_workspace.lock
        skill_dirs = [stock_skills_dir(), home / "skills",
                      Path(meta.cwd) / ".forge" / "skills"]
        web_tools = web_tools_from_config(
            config.serper_api_key, config.firecrawl_api_key)
        memory_tools: list[Tool] = [ReadMemoryTool(home, meta.project_id)]
        if memory_index is not None:
            memory_tools.append(RememberTool(memory_index, meta.project_id))
        # Project-wide semantic file search: only when an embedder-backed index
        # exists and the session belongs to a project (the index is keyed by id).
        file_search_tool = (
            SearchFilesTool(file_index, meta.project_id)
            if file_index is not None and meta.project_id else None)
        agent_extra_tools = list(memory_tools)
        if file_search_tool is not None:
            agent_extra_tools.append(file_search_tool)
        subagents = SpawnAgentsTool(
            llm=llm, skill_dirs=skill_dirs,
            model_fn=lambda: config.subagent_model or self.meta.model,
            effort_fn=lambda: "medium",
            parent_prompt_fn=lambda: self.system_prompt_fn(self.meta),
            max_concurrent=config.max_subagents, max_turns=config.subagent_max_turns,
            web_tools=web_tools, memory_tools=agent_extra_tools)
        image_tool = image_tool_from_config(
            config.openrouter_api_key, config.image_model)
        self.tools = default_tools(skill_dirs, subagents=subagents, web_tools=web_tools,
                                   memory_tools=memory_tools, image_tool=image_tool,
                                   file_search_tool=file_search_tool)
        # Tools gated behind a skill: hidden from the model until that skill is
        # loaded. Maps gated tool name → activating skill name.
        self.skill_gated = {t: skill for t, skill in
                            skill_tool_activations(skill_dirs).items()
                            if t in self.tools}
        # Let load_skill report descriptions of the tools a skill activates.
        for t in self.tools.values():
            if isinstance(t, LoadSkillTool):
                t.tool_descriptions = {
                    name: tool.description for name, tool in self.tools.items()}
        self.session_policies: list[Policy] = []
        # PTY-backed terminals live for the whole session, independent of any
        # run: a terminal opened by one run survives that run's cancellation.
        # An actor holding live terminals is pinned busy so the LRU never evicts
        # it and delete/archive/shutdown reap their process groups first.
        self.terminals = SessionTerminals()
        self.run_task: asyncio.Task | None = None
        self.memory_task: asyncio.Task | None = None
        self.compact_task: asyncio.Task | None = None
        self._approvals: dict[str, asyncio.Future] = {}
        self._plan_gates: dict[str, asyncio.Future] = {}

    def is_busy(self) -> bool:
        """True while a run or a background memory update is live. Used by the
        manager to keep an actor resident (never evict mid-run) and to guard
        deletion."""
        return self._has_active_task() or self.terminals.has_live()

    def _has_active_task(self) -> bool:
        """True while a run/memory/compaction task is live, ignoring terminals.
        Deletion refuses on an active task but reaps live terminals rather than
        being blocked by them."""
        return bool(
            (self.run_task and not self.run_task.done())
            or (self.memory_task and not self.memory_task.done())
            or (self.compact_task and not self.compact_task.done()))

    def teardown(self) -> None:
        """Reap every live terminal's process group. Idempotent; called when the
        session is deleted, archived, or the server shuts down so no PTY child is
        orphaned."""
        self.terminals.reap_all()

    # -- terminals ----------------------------------------------------------
    def _terminal_snapshot(self, term: Terminal) -> None:
        """Append the durable lifecycle snapshot for ``term``'s current state."""
        self.emit(self._e(
            TerminalState, terminal_id=term.id, command=list(term.command),
            cwd=term.cwd, cols=term.cols, rows=term.rows, state=term.state,
            output_offset=term.buffer.end, exit_code=term.exit_code,
            exit_reason=term.exit_reason))

    def _on_terminal_output(self, term: Terminal, text: str, end: int) -> None:
        """Runtime output hook: publish the decoded chunk as an ephemeral event.
        Runs inside the PTY reader — exceptions here would break reading, so the
        runtime already guards this call; keep it side-effect free otherwise."""
        self.publish_ephemeral(self._e(
            TerminalOutput, terminal_id=term.id, start_offset=end - len(text.encode()),
            end_offset=end, text=text))

    def _on_terminal_exit(self, term: Terminal) -> None:
        """Runtime exit hook: persist the final lifecycle snapshot. Guarded by
        the runtime so a failure here never breaks PTY teardown."""
        self._terminal_snapshot(term)

    async def open_terminal(self, command: list[str], cwd: str | None = None, *,
                            cols: int = 80, rows: int = 24) -> Terminal:
        """Open a PTY-backed terminal, wire its output/exit hooks, and emit the
        durable open snapshot. Task 3's BashTool path calls this; there is no
        public REST open endpoint (the UI drives agent-opened terminals only)."""
        term = await self.terminals.open(
            command, cwd or self.meta.cwd, cols=cols, rows=rows,
            on_output=self._on_terminal_output, on_exit=self._on_terminal_exit)
        self._terminal_snapshot(term)
        return term

    def _get_terminal(self, terminal_id: str) -> Terminal:
        try:
            return self.terminals.get(terminal_id)
        except TerminalError:
            raise TerminalNotFound(terminal_id) from None

    def list_terminals(self) -> list[Terminal]:
        return self.terminals.list()

    def read_terminal(self, terminal_id: str, after: int = 0) -> tuple[str, int, int, bool]:
        """Return ``(text, start_offset, end_offset, dropped)`` for output at/after
        ``after``. ``dropped`` is True when ``after`` fell below the retained
        window (older output was evicted from the ring buffer)."""
        term = self._get_terminal(terminal_id)
        start = max(after, term.buffer.start)
        text, end = term.buffer.read(after)
        return text, start, end, after < term.buffer.start

    def write_terminal(self, terminal_id: str, data: str) -> None:
        term = self._get_terminal(terminal_id)
        try:
            term.write(data)
        except TerminalError as e:
            raise TerminalNotRunning(str(e)) from None

    def resize_terminal(self, terminal_id: str, cols: int, rows: int) -> None:
        term = self._get_terminal(terminal_id)
        if term.state != "running":
            raise TerminalNotRunning(f"terminal {terminal_id} is not running")
        if term.cols == cols and term.rows == rows:
            return  # no-op resize: don't emit a redundant durable snapshot
        term.resize(cols, rows)
        self._terminal_snapshot(term)

    def signal_terminal(self, terminal_id: str, sig: int) -> None:
        term = self._get_terminal(terminal_id)
        try:
            term.signal(sig)
        except TerminalError as e:
            raise TerminalNotRunning(str(e)) from None

    def close_terminal(self, terminal_id: str) -> None:
        term = self._get_terminal(terminal_id)
        if term.state == "closed":
            return  # already closed: don't emit a redundant durable snapshot
        term.close()
        self._terminal_snapshot(term)

    def reconcile_terminals(self) -> None:
        """After a restart the real PTY processes are gone but the log may end on
        a still-``running`` TerminalState. Append an ``orphaned`` snapshot for any
        such terminal so replay reaches a coherent, non-live state. Called during
        rehydrate; the runtime registry is empty at that point, so every live
        record in the log is by definition orphaned."""
        latest: dict[str, TerminalState] = {}
        for e in self.log.read():
            if e.type == "terminal_state":
                latest[e.terminal_id] = e
        for snap in latest.values():
            if snap.state not in ("starting", "running"):
                continue
            self.emit(self._e(
                TerminalState, terminal_id=snap.terminal_id, command=snap.command,
                cwd=snap.cwd, cols=snap.cols, rows=snap.rows, state="orphaned",
                output_offset=snap.output_offset, exit_code=None,
                exit_reason="orphaned"))

    # -- event helpers ------------------------------------------------------
    def emit(self, event):
        stamped = self.log.append(event)
        # Keep the cached pill state (latest run reason / unread) on meta in
        # sync: run_finished/run_acknowledged shift it, and a history_rewound can
        # drop the completion off the active branch. The frontend updates live
        # from the events themselves; this cached meta is maintained for REST
        # reads and re-faulting. Re-derive from the active branch so
        # rewinds/acks stay consistent.
        if event.type in ("run_finished", "run_acknowledged", "history_rewound"):
            self._refresh_run_state()
        self.bus.publish(stamped)
        return stamped

    def _refresh_run_state(self) -> None:
        events = self.log.read()
        latest = latest_run(events)
        self.meta.last_run_reason = latest[1] if latest else None
        self.meta.last_run_seq = latest[0] if latest else None
        self.meta.unread = unread_run_seq(events) is not None

    def publish_ephemeral(self, event) -> None:
        self.bus.publish(event)

    def _e(self, cls, **kw):
        return cls(session_id=self.meta.id, ts=time.time(), **kw)

    def _set_status(self, status: Status) -> None:
        if self.meta.status != status:
            self.meta.status = status
            self.emit(self._e(StatusChanged, status=status))

    # -- commands ------------------------------------------------------------
    async def post_message(self, text: str, images: list[str] | None = None) -> None:
        run_live = self.run_task is not None and not self.run_task.done()
        # A message sent while a compaction is in flight must be held, not start
        # a second run racing the summarizer. Ghost it like a steering bubble;
        # compact_now consumes it once the summary lands.
        compacting = self.compact_task is not None and not self.compact_task.done()
        steering = run_live or compacting
        # Publish the bubble first so it renders instantly; the workspace
        # snapshot is a blocking git operation and must not gate the UI.
        ev = self.emit(self._e(UserMessage, text=text, images=images or [],
                               steering=steering))
        self.meta.last_message_at = ev.ts
        if self.meta.name == "New session":
            self.meta.name = text[:40]
            self.emit(self._e(SessionRenamed, name=self.meta.name))
        # Snapshot the tree as it stands before this message's run mutates it,
        # then attach it as the rewind target. Run off the event loop and under
        # the workspace lock so it never blocks the loop or races a live tool
        # mutation / checkpoint restore. Reconcile first so any out-of-band edits
        # since the last activity are folded into the log (and the cursor moves
        # to this tree) before the capture; then record a checkpoint marker tying
        # the rewind point to this message. The run starts only after the
        # snapshot, so the captured tree precedes any tool changes.
        async with self.workspace_lock:
            checkpoint, activity_seq = await asyncio.to_thread(
                self._capture_message_checkpoint, ev.seq)
        self.emit(self._e(
            MessageCheckpointed, user_seq=ev.seq, checkpoint=checkpoint,
            workspace_activity_seq=activity_seq))
        if not compacting and (self.run_task is None or self.run_task.done()):
            self.run_task = asyncio.create_task(self._run())

    def _capture_message_checkpoint(self, user_seq: int) -> tuple[str, int | None]:
        """Reconcile out-of-band drift, capture this message's rewind checkpoint,
        then record a checkpoint activity marker. Runs off the event loop under
        the workspace lock (held by the caller). Reconciliation is best-effort so
        a tracker hiccup never blocks the capture the rewind machinery needs."""
        try:
            self.shared_workspace.reconcile()
        except Exception:
            logger.exception("workspace reconcile before checkpoint failed")
        checkpoint = self.checkpoints.capture().id
        activity_seq: int | None = None
        try:
            marker = self.shared_workspace.record_checkpoint(
                session_id=self.meta.id, user_seq=user_seq, checkpoint=checkpoint)
            activity_seq = marker.seq
        except Exception:
            logger.exception("recording checkpoint activity failed")
        return checkpoint, activity_seq

    def acknowledge(self) -> None:
        """Mark the latest unread completion as read. Idempotent: emits a
        durable RunAcknowledged only when a completion is currently unread, so
        repeated calls (or acking an already-read session) are no-ops that emit
        nothing. Replaying the active branch yields a monotonic read watermark."""
        run_seq = unread_run_seq(self.log.read())
        if run_seq is None:
            return
        self.emit(self._e(RunAcknowledged, run_seq=run_seq))

    def set_autonomy(self, autonomy: Autonomy) -> None:
        self.meta.autonomy = autonomy
        self.emit(self._e(AutonomyChanged, autonomy=autonomy))

    def set_model(self, model: str) -> None:
        self.meta.model = model
        self.emit(self._e(ModelChanged, model=model))

    def set_effort(self, effort: Effort) -> None:
        self.meta.effort = effort
        self.emit(self._e(EffortChanged, effort=effort))

    def set_mode(self, mode: Mode) -> None:
        if self.meta.mode == mode:
            return
        self.meta.mode = mode
        self.emit(self._e(ModeChanged, mode=mode))

    def archive(self) -> bool:
        if self.run_task and not self.run_task.done():
            return False
        # Reap any live terminals: an archived session isn't interactive.
        self.terminals.reap_all()
        self.meta.archived = True
        self.emit(self._e(SessionArchived))
        return True

    def unarchive(self) -> None:
        self.meta.archived = False
        self.emit(self._e(SessionUnarchived))

    def cancel(self) -> None:
        if self.run_task and not self.run_task.done():
            self.run_task.cancel()

    async def resolve_approval(self, call_id: str, decision: str,
                               always: dict | None = None) -> None:
        fut = self._approvals.pop(call_id, None)
        if fut and not fut.done():
            fut.set_result((decision, always))

    async def resolve_plan(self, call_id: str, decision: str, feedback: str = "") -> None:
        fut = self._plan_gates.pop(call_id, None)
        if fut and not fut.done():
            fut.set_result((decision, feedback))

    def _restore_touched_paths(self, target_checkpoint: str) -> set[str]:
        """Canonical absolute path strings the restore to ``target_checkpoint``
        would touch: every path differing between the target tree and the live
        tree, computed ENTIRELY within this session's own checkpoint store (the
        same store that will perform the restore) so the tree objects are always
        present and the diff is authoritative.

        The restore reinstates the exact target tree, so it also DELETES files
        added since the target (a foreign session's newly-added file included);
        such additions are therefore restore-touched and must block.

        Raises ``RewindProvenanceUnavailable`` when the target tree is missing or
        the live tree cannot be snapshotted/diffed — we must never silently treat
        an unknowable diff as empty and proceed to restore."""
        store = self.checkpoints
        try:
            target_tree = store.get(target_checkpoint).tree
        except KeyError as e:
            raise RewindProvenanceUnavailable(
                "workspace provenance unavailable: target checkpoint tree "
                "missing; retry after git/checkpoint recovery") from e
        try:
            current_tree = store.snapshot_tree()
            changes = store.diff_trees(target_tree, current_tree)
        except WorkspaceCheckpointError as e:
            raise RewindProvenanceUnavailable(
                "workspace provenance unavailable: cannot snapshot/diff the live "
                "tree; retry after git/checkpoint recovery") from e
        ws = self.shared_workspace
        return {str(ws.canonical(ws.cwd / rel)) for _status, rel in changes if rel}

    def _rewind_conflicts(self, target_checkpoint: str, target_user_seq: int,
                          events: list) -> list[tuple[str, str]]:
        """Under the workspace lock: return (relative_path, author) conflicts that
        make rewinding to ``target_checkpoint`` unsafe.

        Reconciles out-of-band drift, then computes the canonical paths the
        restore would touch (the diff between the target tree and the live tree,
        computed within this session's own checkpoint store) and scans activity
        recorded after the target message's activity boundary (0 when
        unknown/legacy — a conservative whole-log scan). A restore-touched path
        conflicts when a later record that touched it is ``external`` or is
        attributed to another session. No-path markers (checkpoint/terminal
        launches) and this session's own records are ignored.

        Raises ``RewindProvenanceUnavailable`` (never silently returns empty)
        when the restore-touched paths cannot be reliably computed, so the rewind
        fails closed instead of clobbering unrelated work."""
        ws = self.shared_workspace
        try:
            ws.reconcile()
        except Exception:
            logger.exception("workspace reconcile before rewind gate failed")
        touched = self._restore_touched_paths(target_checkpoint)
        if not touched:
            return []
        boundary = message_activity_boundaries(events).get(target_user_seq)
        after_seq = boundary or 0
        conflicts: list[tuple[str, str]] = []
        seen: set[str] = set()
        for rec in ws.activity.read(after_seq=after_seq):
            if not rec.paths:
                continue  # no-path provenance markers claim nothing to clobber
            foreign = (rec.origin == "external"
                       or (rec.session_id is not None
                           and rec.session_id != self.meta.id))
            if not foreign:
                continue  # own edits are being rewound; benign None-session too
            for p in rec.paths:
                if p in touched and p not in seen:
                    seen.add(p)
                    author = (f"session {rec.session_id}" if rec.session_id
                              else rec.origin)
                    try:
                        rel = str(Path(p).relative_to(ws.cwd))
                    except ValueError:
                        rel = p
                    conflicts.append((rel, author))
        return conflicts

    async def rewind(self, target_user_seq: int, text: str | None = None,
                     images: list[str] | None = None) -> None:
        """Append-only rewind of the active branch to just before
        ``target_user_seq``. With text/images: atomic edit-and-resend. Without:
        rewind-only. Never truncates the log."""
        replacement = text is not None or bool(images)
        if replacement and not (text or "").strip() and not images:
            raise RewindEmptyReplacement("replacement text/images required")
        events = self.log.read()
        target = next((e for e in events if e.seq == target_user_seq
                       and e.type == "user_message"), None)
        if target is None:
            raise RewindTargetMissing(f"no user message at seq {target_user_seq}")
        if target_user_seq not in active_user_seqs(events):
            raise RewindTargetInactive(
                f"user message {target_user_seq} is not on the active branch")
        target_checkpoint = message_checkpoints(events).get(target_user_seq)
        if not target_checkpoint:
            raise RewindNoCheckpoint(
                f"user message {target_user_seq} has no workspace checkpoint")

        # Cancel and await any live run so no events append after the marker.
        if self.run_task and not self.run_task.done():
            self.run_task.cancel()
            with contextlib.suppress(BaseException):
                await self.run_task
        # Resolve dangling approval / plan gates so awaiters unblock safely.
        for fut in list(self._approvals.values()):
            if not fut.done():
                fut.set_result(("deny", None))
        self._approvals.clear()
        for fut in list(self._plan_gates.values()):
            if not fut.done():
                fut.set_result(("revise", ""))
        self._plan_gates.clear()
        # Stop an abandoned background memory update for the discarded branch.
        if self.memory_task and not self.memory_task.done():
            self.memory_task.cancel()
            with contextlib.suppress(BaseException):
                await self.memory_task
        self.memory_task = None
        # Stop an in-flight compaction whose branch is being discarded.
        if self.compact_task and not self.compact_task.done():
            self.compact_task.cancel()
            with contextlib.suppress(BaseException):
                await self.compact_task
        self.compact_task = None
        self.run_task = None

        async with self.workspace_lock:
            # Conflict gate: refuse a rewind that would clobber another session's
            # or an external process's work on a path the restore would touch.
            # Runs under the lock (so reconcile/tree reads are consistent) and
            # BEFORE any destructive step — safety capture, intent write, restore,
            # and closing live terminals — so a refused rewind leaves the session,
            # tree, and terminals untouched.
            conflicts = self._rewind_conflicts(
                target_checkpoint, target_user_seq, events)
            if conflicts:
                raise RewindConflict(conflicts)

            # Kill any live terminal's process group before the workspace restore:
            # a rewind rewrites the tree out from under running processes, so we
            # tear them down first and emit a final durable ``closed`` snapshot
            # for each. Only lifecycle metadata is durable; the raw byte stream
            # stays ephemeral. V1 semantics: every live terminal in the session
            # is closed (terminals are session-scoped, not branch-scoped).
            for term in self.terminals.list():
                if term.is_live():
                    term.close()
                    self._terminal_snapshot(term)

            try:
                safety = self.checkpoints.capture(
                    label=f"pre-rewind seq {target_user_seq}")
            except WorkspaceCheckpointError as e:
                raise RewindWorkspaceError(str(e)) from e
            # Record the intent atomically before the first destructive restore.
            # If we crash between here and the durable marker, rehydrate replays
            # this to reach a coherent state (either the safety branch or the
            # fully-applied rewind).
            self.rewind_intent.write(RewindIntent(
                target_user_seq=target_user_seq,
                target_checkpoint=target_checkpoint,
                safety_checkpoint=safety.id, replacement=replacement,
                replacement_text=text or "", replacement_images=images or []))
            # Baseline the live tree so the successful restore can be attributed
            # as a ``rewind`` activity (and the cursor advanced) once it lands.
            before_tree = self.shared_workspace.begin_tree()
            try:
                # Restore first; only append the marker once restore succeeds so a
                # failed restore (which internally rolls back) leaves no marker.
                self.checkpoints.restore(target_checkpoint)
            except WorkspaceCheckpointError as e:
                # No destructive change survived (restore rolled itself back);
                # the old branch is intact so drop the intent.
                self.rewind_intent.clear()
                raise RewindWorkspaceError(str(e)) from e
            # Attribute the restore to this session so a later reconcile does not
            # relabel the restored content as external, and the cursor tracks the
            # restored tree. Best-effort: recording must not fail the rewind.
            try:
                self.shared_workspace.record_tree_change(
                    before_tree, origin="rewind", action="rewind",
                    session_id=self.meta.id,
                    note=f"rewind to seq {target_user_seq}")
            except Exception:
                logger.exception("recording rewind tree change failed")
            try:
                self.emit(self._e(
                    HistoryRewound, target_user_seq=target_user_seq,
                    target_checkpoint=target_checkpoint,
                    safety_checkpoint=safety.id, replacement=replacement))
            except Exception:
                # The marker never landed, so the log still describes the live
                # branch. Undo the workspace restore so tree and history stay
                # coherent, drop the intent, then surface the failure.
                self.checkpoints.restore(safety.id)
                self.rewind_intent.clear()
                # Move the cursor to the rolled-back (safety) tree so a later
                # reconcile does not mislabel the restore-then-rollback churn as
                # an external change.
                try:
                    self.shared_workspace.advance_cursor()
                except Exception:
                    logger.exception("post-rollback cursor advance failed")
                raise
        if self.meta.status != "idle":
            self._set_status("idle")
        if replacement:
            # Reuse the restored target checkpoint: the tree is already what a
            # fresh capture would produce, so don't snapshot an identical one.
            ev = self.emit(self._e(
                UserMessage, text=text or "", images=images or [],
                steering=False, workspace_checkpoint=target_checkpoint))
            self.meta.last_message_at = ev.ts
            # Marker and replacement message are both durable now.
            self.rewind_intent.clear()
            self.run_task = asyncio.create_task(self._run())
        else:
            # Marker is durable and no replacement is needed.
            self.rewind_intent.clear()
            active = active_events(self.log.read())
            last_msg = next(
                (e for e in reversed(active)
                 if e.type in ("user_message", "assistant_message")), None)
            self.meta.last_message_at = last_msg.ts if last_msg else None

    def recover_rewind(self) -> None:
        """Replay an interrupted rewind recorded by ``rewind_intent`` so the log
        and workspace reach a coherent state after a crash. Called synchronously
        during rehydrate, before mid-run detection. Never calls the LLM.

        - Marker never landed: the log still describes the old branch. Restore
          the safety checkpoint and drop the intent.
        - Marker landed: ensure the target checkpoint is restored. If a
          replacement was requested but its user message was not durably
          appended after the marker, append it (mid-run detection then marks it
          interrupted). Drop the intent.
        """
        intent = self.rewind_intent.read()
        if intent is None:
            return
        events = self.log.read()
        marker = next(
            (e for e in reversed(events)
             if e.type == "history_rewound"
             and e.target_user_seq == intent.target_user_seq
             and e.target_checkpoint == intent.target_checkpoint
             and e.safety_checkpoint == intent.safety_checkpoint), None)
        try:
            if marker is None:
                # Crash after the destructive restore but before the marker: the
                # history is still the old branch, so return the tree to the
                # pre-rewind safety snapshot to match it.
                self.checkpoints.restore(intent.safety_checkpoint)
            else:
                # The rewind is durable in the log; make the tree match the
                # target and finish the optional replacement append.
                self.checkpoints.restore(intent.target_checkpoint)
                if intent.replacement and not any(
                        e.type == "user_message" and e.seq > marker.seq
                        for e in events):
                    ev = self.emit(self._e(
                        UserMessage, text=intent.replacement_text,
                        images=intent.replacement_images, steering=False,
                        workspace_checkpoint=intent.target_checkpoint))
                    self.meta.last_message_at = ev.ts
        except WorkspaceCheckpointError:
            # Surface the failure but keep the intent as evidence so a later
            # recovery attempt (or an operator) can still act on it.
            logger.error(
                "rewind recovery failed for session %s (target seq %s); "
                "leaving intent in place", self.meta.id, intent.target_user_seq,
                exc_info=True)
            return
        self.rewind_intent.clear()

    # -- run loop -------------------------------------------------------------
    async def _run(self) -> None:
        try:
            # Cancel may arrive while awaiting the semaphore (session still
            # "queued"); the try must wrap the slot acquisition too.
            async with self.scheduler.slot(lambda: self._set_status("queued")):
                self._set_status("running")
                # Heal any tool_use left unresolved by a prior run that died
                # abnormally (crash, restart) or by an edit-and-resend whose
                # rewind target sits AFTER the orphan (so the rewind doesn't
                # discard it). Covers every run entry point — post_message,
                # rewind replacement, crash recovery — in one place. Projection
                # defers the new user message past the open call, so the
                # synthetic result still lands in valid order.
                self._close_dangling("Previous run ended before this tool returned")
                # Anchor the memory pass to the last run boundary on the active
                # branch: a rewind discards the abandoned branch's run_finished
                # markers, so the audit log's last one may not apply here.
                run_start_seq = max(
                    (e.seq for e in active_events(self.log.read())
                     if e.type == "run_finished"), default=0)
                await self._loop()
                self._start_memory_update(run_start_seq)
                # Successful completions are unread until the user sees them.
                self.emit(self._e(RunFinished, reason="completed", unread=True))
        except asyncio.CancelledError:
            self._close_dangling("Cancelled by user")
            self.emit(self._e(RunFinished, reason="cancelled"))
        except LLMError as e:
            self._close_dangling("Run errored")
            self.emit(self._e(ErrorEvent, message=str(e)))
            self.emit(self._e(RunFinished, reason="error"))
        except Exception as e:  # backstop: projection/summarizer/other crashes
            self._close_dangling("Run errored")
            self.emit(self._e(ErrorEvent, message=f"Unexpected error: {e!r}"))
            self.emit(self._e(RunFinished, reason="error"))
        finally:
            self._set_status("idle")

    async def _recall_memories(self) -> None:
        """Retrieve memory snippets for user messages that don't have a recall
        event yet. Best-effort: retrieval failures never block the run."""
        if self.memory_index is None:
            return
        events = active_events(self.log.read())
        done = {e.user_seq for e in events if e.type == "memory_recalled"}
        cut = max((e.upto_seq for e in events if e.type == "context_compacted"),
                  default=0)
        for e in events:
            if e.type != "user_message" or e.seq <= cut or e.seq in done \
                    or not e.text.strip():
                continue
            try:
                snippets = await self.memory_index.search(
                    e.text, self.meta.project_id)
            except Exception:
                # Best-effort: log for observability but never block the run.
                # Skipped messages retry on the next turn.
                logger.warning(
                    "memory recall failed for session %s user_seq %s",
                    self.meta.id, e.seq, exc_info=True)
                continue
            self.emit(self._e(
                MemoryRecalled, user_seq=e.seq,
                snippets=[RecalledSnippet(
                    tier=s.tier, region=s.region, start_line=s.start_line,
                    end_line=s.end_line, text=s.text, score=s.score)
                    for s in snippets]))

    async def _loop(self) -> None:
        # context_seq of the previous completion, so a completion can tell which
        # steering messages IT is the first to consume (seq in that open window).
        prev_ctx = self.log.last_seq
        while True:
            start_seq = self.log.last_seq
            await self._recall_memories()

            async def on_delta(text: str) -> None:
                self.publish_ephemeral(self._e(TextDelta, text=text))

            async def on_tool_start(call_id: str, tool: str) -> None:
                self.publish_ephemeral(
                    self._e(ToolCallPending, call_id=call_id, tool=tool))

            context_seq = self.log.last_seq
            # A steering message queued since the last completion is now part of
            # this completion's context. Un-ghost its bubble the moment the
            # request goes out, not when the reply lands (which is a whole
            # turn later). The durable AssistantMessage.context_seq re-does this
            # on replay, so history renders identically.
            if any(e.type == "user_message" and e.steering
                   and prev_ctx < e.seq <= context_seq
                   for e in self.log.read(after_seq=prev_ctx)):
                self.publish_ephemeral(
                    self._e(SteeringConsumed, context_seq=context_seq))
            prev_ctx = context_seq
            # Freeze model attribution at request dispatch. Session model changes
            # while this completion streams must not relabel its emitted tools.
            completion_model = self.meta.model
            result = await self.llm.complete(
                completion_model,
                to_messages(self.log.read(), self.system_prompt_fn(self.meta),
                            completion_model),
                [openai_spec(t) for t in self._active_tools()],
                on_delta, effort=self.meta.effort, on_tool_start=on_tool_start)
            ev = self.emit(self._e(AssistantMessage, text=result.text,
                                   tool_calls=result.tool_calls,
                                   usage_tokens=result.usage_tokens,
                                   context_seq=context_seq))
            self.meta.last_message_at = ev.ts
            if not result.tool_calls:
                if any(e.type == "user_message" and e.seq > start_seq
                       for e in self.log.read(after_seq=start_seq)):
                    continue  # steering arrived during final stream
                return
            for call in result.tool_calls:
                await self._execute_call(call, completion_model)
            await self._maybe_compact(result.usage_tokens)

    def _active_tools(self) -> list[Tool]:
        """Plan mode offers only read-only tools (plus spawn_agents, whose tasks
        are forced read-only at execution). Bash is excluded: it can mutate.
        Skill-gated tools are hidden until their activating skill is loaded."""
        loaded = loaded_skill_names(self.log.read()) if self.skill_gated else set()
        tools = [t for t in self.tools.values()
                 if self.skill_gated.get(t.name, None) in (None, *loaded)]
        if self.meta.mode != "plan":
            return tools
        return [t for t in tools if t.read_only or isinstance(t, SpawnAgentsTool)]

    def _run_transcript(self, after_seq: int) -> str:
        lines: list[str] = []
        for event in active_events(self.log.read()):
            if event.seq <= after_seq:
                continue
            if event.type == "user_message":
                lines.append(f"USER: {event.text}")
            elif event.type == "assistant_message" and event.text:
                lines.append(f"ASSISTANT: {event.text}")
            elif event.type == "tool_call_started":
                lines.append(f"TOOL CALL: {event.tool} — {event.display}")
            elif event.type == "tool_call_finished":
                status = "ERROR" if event.is_error else "RESULT"
                lines.append(f"TOOL {status} ({event.tool}): {event.output}")
        return "\n\n".join(lines)

    def _start_memory_update(self, after_seq: int) -> None:
        """Kick off the memory pass in the background: it must never delay
        RunFinished or block a follow-up message from starting the next run."""
        if self.memory_agent is None:
            return
        # Snapshot the transcript now so the next run's events can't leak in.
        transcript = self._run_transcript(after_seq)
        if not transcript:
            return

        async def update() -> None:
            self.publish_ephemeral(self._e(MemoryUpdate, state="running"))
            try:
                written = await self.memory_agent.update(
                    self.meta.project_id,
                    self.config.memory_model or self.meta.model,
                    self.meta.effort, transcript)
            except Exception:
                # Memory enrichment is best-effort and must never turn a
                # successful user run into an error. The next completed run
                # can recover.
                self.publish_ephemeral(self._e(MemoryUpdate, state="error"))
                return
            self.publish_ephemeral(self._e(
                MemoryUpdate, state="written" if written else "unchanged"))

        self.memory_task = asyncio.create_task(update())

    async def _persist_subagent_grade(self, record: SubagentGradeRecord) -> None:
        """Callback handed to spawn_agents so the tool never owns the global
        store. Stamps session/project identity, then appends. Best-effort: a
        persistence failure is logged but never propagated (it must not fail the
        worker or the parent run)."""
        record.session_id = self.meta.id
        record.project_id = self.meta.project_id
        try:
            await _grade_store(self.home).append(record)
        except Exception:
            logger.warning("subagent grade persist failed for session %s",
                           self.meta.id, exc_info=True)

    async def _execute_call(self, call: ToolCallSpec,
                            completion_model: str | None = None) -> None:
        tool = self.tools.get(call.name)
        if tool is None:
            self.emit(self._e(ToolCallFinished, call_id=call.id, tool=call.name,
                              output=f"Unknown tool: {call.name}", is_error=True))
            return
        try:
            args = json.loads(call.arguments or "{}")
        except json.JSONDecodeError as e:
            self.emit(self._e(ToolCallFinished, call_id=call.id, tool=call.name,
                              output=f"Invalid tool arguments JSON: {e}", is_error=True))
            return
        if call.name == PLAN_TOOL_NAME:
            await self._plan_gate(call, args)
            return
        if self.meta.mode == "plan":
            if isinstance(tool, SpawnAgentsTool):
                # Plan mode: workers explore only — force every task read-only.
                for item in args.get("tasks") or []:
                    if isinstance(item, dict):
                        item["mode"] = "read"
            elif not tool.read_only:
                self.emit(self._e(
                    ToolCallFinished, call_id=call.id, tool=call.name,
                    output="Blocked: session is in plan mode. Only read-only tools "
                           "are available; finish by calling propose_plan.",
                    is_error=True))
                return
        # display/requires_approval run before the tool's own try/except below,
        # so a bug (or malformed args) here would otherwise crash the whole run
        # instead of feeding an error back to the model.
        try:
            display = tool.display(args)
            needs_approval = tool.requires_approval(args)
        except Exception as e:
            self.emit(self._e(ToolCallFinished, call_id=call.id, tool=call.name,
                              output=f"Invalid tool arguments: {e!r}", is_error=True))
            return

        auto = False
        if needs_approval:
            policies = self.config.policies + self.session_policies
            if policy_matches(policies, call.name, display):
                auto = True
            elif self.meta.autonomy == "yolo":
                auto = True
            else:
                allowed = await self._gate(call, display)
                if not allowed:
                    return

        self.emit(self._e(ToolCallStarted, call_id=call.id, tool=call.name,
                          display=display, auto_approved=auto))
        # Snapshot the parent turn context up to this call before the tool runs,
        # so a subagent grader sees the active run's user/assistant/tool activity
        # available at spawn time — not future events. Anchor to the last run
        # boundary so a rewind's discarded branch doesn't leak in.
        parent_context = ""
        if isinstance(tool, SpawnAgentsTool):
            run_start = max((e.seq for e in active_events(self.log.read())
                             if e.type == "run_finished"), default=0)
            parent_context = self._run_transcript(run_start)
        ctx = ToolContext(
            cwd=Path(self.meta.cwd),
            emit_chunk=lambda t: self.publish_ephemeral(
                self._e(OutputChunk, call_id=call.id, text=t)),
            emit_event=lambda **kw: self.publish_ephemeral(
                self._e(SubagentUpdate, call_id=call.id, **kw)),
            emit_subagent_state=lambda **kw: self.emit(
                self._e(SubagentState, call_id=call.id, **kw)),
            changesets=self.changesets,
            terminals=ActorTerminalController(self),
            persist_subagent_grade=self._persist_subagent_grade,
            parent_context=parent_context,
            orchestrator_model=(completion_model
                                if isinstance(tool, SpawnAgentsTool) else None),
            call_id=call.id,
            session_id=self.meta.id,
            shared_workspace=self.shared_workspace)
        started = time.monotonic()
        finite_bash = (isinstance(tool, BashTool)
                       and not args.get("display_terminal"))
        try:
            if tool.read_only:
                result = await tool.run(args, ctx)
            elif tool.manages_workspace_lock:
                # The tool dispatches its own workspace mutations (spawn_agents
                # write workers) and acquires the shared lock per mutating call;
                # wrapping it here would deadlock those workers on the same lock.
                result = await tool.run(args, ctx)
            elif finite_bash:
                # Bracket a finite (run-to-completion) bash with a whole-tree
                # snapshot before/after so any file it touched is attributed to
                # this session/call even though bash writes are opaque to Forge.
                async with self.workspace_lock:
                    before_tree = self.shared_workspace.begin_tree()
                    try:
                        result = await tool.run(args, ctx)
                    finally:
                        # Record on every exit — success, nonzero, timeout,
                        # exception, or cancellation — so a mutation is never
                        # lost. Best-effort: recording must not mask the tool's
                        # own outcome or a cancellation.
                        try:
                            self.shared_workspace.record_tree_change(
                                before_tree, origin="bash", action="bash",
                                session_id=self.meta.id, call_id=call.id)
                        except Exception:
                            logger.exception("bash tree reconcile failed")
            else:
                # Mutating tools serialize against checkpoint capture/restore.
                async with self.workspace_lock:
                    result = await tool.run(args, ctx)
                # A persistent-terminal launch records a marker (no changed paths)
                # so provenance notes this session/call started it; async writes
                # surface as external on a future reconcile.
                if (isinstance(tool, BashTool) and args.get("display_terminal")
                        and not result.is_error):
                    try:
                        self.shared_workspace.record_terminal_launch(
                            session_id=self.meta.id, call_id=call.id,
                            note=result.output[:200] if result.output else None)
                    except Exception:
                        logger.exception("recording terminal launch failed")
        except asyncio.CancelledError:
            raise
        except Exception as e:  # tool bug → feed back, don't kill the run
            result_output, is_error, stats, todos, images = \
                f"Tool crashed: {e!r}", True, None, None, []
        else:
            result_output, is_error = result.output, result.is_error
            stats, todos, images = result.diff_stats, result.todos, result.images
        self.emit(self._e(
            ToolCallFinished, call_id=call.id, tool=call.name,
            output=result_output or "(no output)", is_error=is_error,
            duration_ms=int((time.monotonic() - started) * 1000), diff_stats=stats,
            images=images))
        if not is_error and todos is not None:
            self.emit(self._e(TodosUpdated, todos=todos))

    async def _plan_gate(self, call: ToolCallSpec, args: dict) -> None:
        """propose_plan: durable proposal, await the user's approve/revise."""
        plan = args.get("plan")
        if not isinstance(plan, str) or not plan.strip():
            self.emit(self._e(ToolCallFinished, call_id=call.id, tool=call.name,
                              output="plan must be a non-empty string", is_error=True))
            return
        self.emit(self._e(PlanProposed, call_id=call.id, plan=plan))
        self._set_status("attention")
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._plan_gates[call.id] = fut
        try:
            decision, feedback = await fut
        finally:
            self._plan_gates.pop(call.id, None)
            self._set_status("running")
        self.emit(self._e(PlanResolved, call_id=call.id, decision=decision,
                          feedback=feedback))
        if decision == "approve":
            self.set_mode("act")
            output = ("Plan approved. You are now in act mode — execute the plan. "
                      "Start by calling update_todos with the plan's steps.")
        else:
            output = f"User requested changes to the plan: {feedback}"
        self.emit(self._e(ToolCallFinished, call_id=call.id, tool=call.name,
                          output=output))

    async def _gate(self, call: ToolCallSpec, display: str) -> bool:
        self.emit(self._e(ApprovalRequested, call_id=call.id, tool=call.name,
                          display=display))
        self._set_status("attention")
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._approvals[call.id] = fut
        try:
            decision, always = await fut
        finally:
            self._approvals.pop(call.id, None)
            self._set_status("running")
        self.emit(self._e(ApprovalResolved, call_id=call.id, decision=decision))
        if always and decision == "allow":
            policy = Policy(tool=call.name, pattern=always["pattern"])
            scope = always.get("scope", "session")
            if scope == "global":
                save_global_policy(self.home, policy)
                self.config.policies.append(policy)
            else:
                self.session_policies.append(policy)
            self.emit(self._e(PolicyAdded, tool=policy.tool, pattern=policy.pattern,
                              scope=scope))
        if decision == "deny":
            self.emit(self._e(ToolCallFinished, call_id=call.id, tool=call.name,
                              output="User denied this action.", is_error=True))
            return False
        return True

    async def _maybe_compact(self, usage_tokens: int) -> None:
        window = self.config.context_window(self.meta.model)
        if usage_tokens <= COMPACT_THRESHOLD * window:
            return
        await self._compact()

    async def compact_now(self) -> bool:
        """Manual /compact. Refused while a run or another compaction is active."""
        if self.run_task and not self.run_task.done():
            return False
        if self.compact_task and not self.compact_task.done():
            return False
        self.compact_task = asyncio.create_task(self._compact())
        await self.compact_task
        # A message posted while the summarizer was in flight was held (ghosted);
        # consume it now by starting a run, unless one is already live.
        cut = max((e.upto_seq for e in active_events(self.log.read())
                   if e.type == "context_compacted"), default=0)
        held = any(e.type == "user_message" and e.seq > cut
                   for e in active_events(self.log.read()))
        if held and (self.run_task is None or self.run_task.done()):
            self.run_task = asyncio.create_task(self._run())
        return True

    async def _compact(self) -> None:
        self.publish_ephemeral(
            self._e(CompactionState, state="running", phase=0, label="Analyzing"))
        try:
            msgs = to_messages(self.log.read(), "", self.meta.model)[1:]  # drop system stub
            transcript = "\n".join(
                f"{m['role'].upper()}: {m.get('content') or m.get('tool_calls', '')}"
                for m in msgs)

            # Drive a determinate progress display: the summary emits the nine
            # COMPACT_SECTIONS in order, so each header crossing advances a phase.
            buf: list[str] = []
            reached = 0

            async def on_delta(text: str) -> None:
                nonlocal reached
                buf.append(text)
                whole = "".join(buf)
                for i in range(reached, len(COMPACT_SECTIONS)):
                    if f"{i + 1}. {COMPACT_SECTIONS[i]}" in whole:
                        reached = i + 1
                        self.publish_ephemeral(self._e(
                            CompactionState, state="running", phase=reached,
                            label=COMPACT_SECTIONS[i]))

            # Capture the cut point BEFORE the summarizer await: a steering message
            # posted while the summarizer is in flight must survive projection.
            upto = self.log.last_seq
            summary = await self.llm.complete(
                self.config.compaction_model or self.meta.model,
                [{"role": "user", "content": COMPACT_PROMPT
                  + "\n\nHere is the conversation to summarize:\n\n" + transcript}],
                [], on_delta, effort=self.meta.effort)
            self.emit(self._e(ContextCompacted, summary=_summary_body(summary.text),
                              upto_seq=upto))
        finally:
            self.publish_ephemeral(self._e(CompactionState, state="done"))

    def _close_dangling(self, reason: str) -> None:
        for call_id, tool in dangling_call_ids(self.log.read()):
            self.emit(self._e(ToolCallFinished, call_id=call_id, tool=tool,
                              output=f"[{reason} — no result]", is_error=True))
