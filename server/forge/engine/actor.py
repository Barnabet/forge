from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Callable

from pydantic import BaseModel

from forge.engine.bus import EventBus
from forge.engine.memory import ProjectMemory
from forge.engine.events import (
    ApprovalRequested, ApprovalResolved, AssistantMessage, Autonomy,
    AutonomyChanged, ContextCompacted, Effort, EffortChanged, ErrorEvent, ModelChanged,
    OutputChunk, PolicyAdded, RunFinished, SessionArchived, SessionRenamed,
    SessionUnarchived, Status, StatusChanged,
    TextDelta, ToolCallFinished, ToolCallPending, ToolCallSpec, ToolCallStarted,
    UserMessage,
)
from forge.engine.projection import dangling_call_ids, to_messages
from forge.engine.scheduler import Scheduler
from forge.llm.base import LLMClient, LLMError
from forge.store.changesets import ChangesetStore
from forge.store.config import ForgeConfig, Policy, policy_matches, save_global_policy
from forge.store.eventlog import EventLog
from forge.tools.base import ToolContext, openai_spec
from forge.tools.registry import default_tools
from forge.tools.subagents import SpawnAgentsTool

COMPACT_THRESHOLD = 0.75


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


class SessionActor:
    def __init__(self, meta: SessionMeta, home: Path, config: ForgeConfig,
                 llm: LLMClient, bus: EventBus, scheduler: Scheduler,
                 system_prompt_fn: Callable[[SessionMeta], str],
                 project_memory: ProjectMemory | None = None):
        self.meta = meta
        self.home = home
        self.config = config
        self.llm = llm
        self.bus = bus
        self.scheduler = scheduler
        self.system_prompt_fn = system_prompt_fn
        self.project_memory = project_memory
        sdir = home / "sessions" / meta.id
        self.log = EventLog(sdir / "events.jsonl")
        self.changesets = ChangesetStore(sdir)
        skill_dirs = [home / "skills", Path(meta.cwd) / ".forge" / "skills"]
        subagents = SpawnAgentsTool(
            llm=llm, skill_dirs=skill_dirs,
            model_fn=lambda: self.meta.model, effort_fn=lambda: self.meta.effort,
            parent_prompt_fn=lambda: self.system_prompt_fn(self.meta),
            max_concurrent=config.max_subagents, max_turns=config.subagent_max_turns)
        self.tools = default_tools(skill_dirs, subagents=subagents)
        self.session_policies: list[Policy] = []
        self.run_task: asyncio.Task | None = None
        self._approvals: dict[str, asyncio.Future] = {}

    # -- event helpers ------------------------------------------------------
    def emit(self, event):
        stamped = self.log.append(event)
        self.bus.publish(stamped)
        return stamped

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
        self.emit(self._e(UserMessage, text=text, images=images or []))
        if self.meta.name == "New session":
            self.meta.name = text[:40]
            self.emit(self._e(SessionRenamed, name=self.meta.name))
        if self.run_task is None or self.run_task.done():
            self.run_task = asyncio.create_task(self._run())

    def set_autonomy(self, autonomy: Autonomy) -> None:
        self.meta.autonomy = autonomy
        self.emit(self._e(AutonomyChanged, autonomy=autonomy))

    def set_model(self, model: str) -> None:
        self.meta.model = model
        self.emit(self._e(ModelChanged, model=model))

    def set_effort(self, effort: Effort) -> None:
        self.meta.effort = effort
        self.emit(self._e(EffortChanged, effort=effort))

    def archive(self) -> bool:
        if self.run_task and not self.run_task.done():
            return False
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

    # -- run loop -------------------------------------------------------------
    async def _run(self) -> None:
        try:
            # Cancel may arrive while awaiting the semaphore (session still
            # "queued"); the try must wrap the slot acquisition too.
            async with self.scheduler.slot(lambda: self._set_status("queued")):
                self._set_status("running")
                run_start_seq = max(
                    (e.seq for e in self.log.read() if e.type == "run_finished"), default=0)
                await self._loop()
                await self._update_project_memory(run_start_seq)
                self.emit(self._e(RunFinished, reason="completed"))
        except asyncio.CancelledError:
            self._close_dangling("Cancelled by user")
            self.emit(self._e(RunFinished, reason="cancelled"))
        except LLMError as e:
            self.emit(self._e(ErrorEvent, message=str(e)))
            self.emit(self._e(RunFinished, reason="error"))
        except Exception as e:  # backstop: projection/summarizer/other crashes
            self.emit(self._e(ErrorEvent, message=f"Unexpected error: {e!r}"))
            self.emit(self._e(RunFinished, reason="error"))
        finally:
            self._set_status("idle")

    async def _loop(self) -> None:
        while True:
            start_seq = self.log.last_seq

            async def on_delta(text: str) -> None:
                self.publish_ephemeral(self._e(TextDelta, text=text))

            async def on_tool_start(call_id: str, tool: str) -> None:
                self.publish_ephemeral(
                    self._e(ToolCallPending, call_id=call_id, tool=tool))

            result = await self.llm.complete(
                self.meta.model,
                to_messages(self.log.read(), self.system_prompt_fn(self.meta)),
                [openai_spec(t) for t in self.tools.values()],
                on_delta, effort=self.meta.effort, on_tool_start=on_tool_start)
            self.emit(self._e(AssistantMessage, text=result.text,
                              tool_calls=result.tool_calls,
                              usage_tokens=result.usage_tokens))
            if not result.tool_calls:
                if any(e.type == "user_message" and e.seq > start_seq
                       for e in self.log.read(after_seq=start_seq)):
                    continue  # steering arrived during final stream
                return
            for call in result.tool_calls:
                await self._execute_call(call)
            await self._maybe_compact(result.usage_tokens)

    def _run_transcript(self, after_seq: int) -> str:
        lines: list[str] = []
        for event in self.log.read(after_seq=after_seq):
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

    async def _update_project_memory(self, after_seq: int) -> None:
        if self.project_memory is None or self.meta.project_id is None:
            return
        transcript = self._run_transcript(after_seq)
        if not transcript:
            return
        try:
            await self.project_memory.update(
                self.meta.project_id, self.meta.model, self.meta.effort, transcript)
        except Exception:
            # Memory enrichment is best-effort and must never turn a successful
            # user run into an error. The next completed run can recover.
            return

    async def _execute_call(self, call: ToolCallSpec) -> None:
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
        display = tool.display(args)

        auto = False
        if tool.requires_approval(args):
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
        ctx = ToolContext(
            cwd=Path(self.meta.cwd),
            emit_chunk=lambda t: self.publish_ephemeral(
                self._e(OutputChunk, call_id=call.id, text=t)),
            changesets=self.changesets)
        started = time.monotonic()
        try:
            result = await tool.run(args, ctx)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # tool bug → feed back, don't kill the run
            result_output, is_error, stats = f"Tool crashed: {e!r}", True, None
        else:
            result_output, is_error, stats = result.output, result.is_error, result.diff_stats
        self.emit(self._e(
            ToolCallFinished, call_id=call.id, tool=call.name,
            output=result_output or "(no output)", is_error=is_error,
            duration_ms=int((time.monotonic() - started) * 1000), diff_stats=stats))

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
        """Manual /compact. Refused while a run is active."""
        if self.run_task and not self.run_task.done():
            return False
        await self._compact()
        return True

    async def _compact(self) -> None:
        msgs = to_messages(self.log.read(), "")[1:]  # drop system stub
        transcript = "\n".join(
            f"{m['role'].upper()}: {m.get('content') or m.get('tool_calls', '')}"
            for m in msgs)[-200_000:]

        async def no_delta(_: str) -> None: ...

        # Capture the cut point BEFORE the summarizer await: a steering message
        # posted while the summarizer is in flight must survive projection.
        upto = self.log.last_seq
        summary = await self.llm.complete(
            self.meta.model,
            [{"role": "user", "content":
              "Summarize this agent session so far for continuation. Include the "
              "original task, key decisions, files touched, current progress, and "
              "immediate next steps.\n\n" + transcript}],
            [], no_delta, effort=self.meta.effort)
        self.emit(self._e(ContextCompacted, summary=summary.text, upto_seq=upto))

    def _close_dangling(self, reason: str) -> None:
        for call_id, tool in dangling_call_ids(self.log.read()):
            self.emit(self._e(ToolCallFinished, call_id=call_id, tool=tool,
                              output=f"[{reason} — no result]", is_error=True))
