from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from pathlib import Path

from forge.engine.events import ToolCallSpec
from forge.engine.skills import load_skill_body
from forge.llm.base import LLMClient
from forge.store.subagent_grades import SubagentGradeRecord
from forge.tools.base import Tool, ToolContext, ToolResult, openai_spec, truncate_middle
from forge.tools.bash import BashTool
from forge.tools.files_read import ReadFileTool
from forge.tools.files_write import EditFileTool, WriteFileTool
from forge.tools.search import GlobTool, GrepTool, ListDirTool
from forge.tools.skills_tool import LoadSkillTool
from forge.tools.subagent_grader import (
    GRADER_MODEL, WorkerCrashed, WorkerRun, build_grader_messages, parse_grade,
)

logger = logging.getLogger(__name__)

_MAX_TASKS = 4
_MAX_RESULT_CHARS = 30_000
_MAX_REPORT_EXCERPT = 4_000
_TURN_WARNING_THRESHOLD = 3  # warn the worker during its final turns
# Direct/unit-test invocations may not have an actor-provided SharedWorkspace.
# Keep their actual mutations mutually exclusive per cwd without serializing the
# workers' model calls, reads, or grading.
_MUTATION_LOCKS: dict[str, asyncio.Lock] = {}


def _rel_display(display: str, cwd: Path) -> str:
    # Mirror the frontend's relDisplay so subagent tool lines show paths
    # relative to the session cwd, like main-agent tool calls do.
    prefix = f"{cwd}/"
    out = display.replace(prefix, "")
    return out or "."

WORKER_PROMPT = """\
{parent_prompt}

## Subagent role
You are a focused worker delegated one task by a parent agent.
- Work only on the delegated task below.
- Do not ask the user questions; investigate and make reasonable assumptions.
- Return a concise report with findings, evidence, files inspected or changed, and any remaining risk.
- You cannot create further subagents.
- Access mode: {mode}.
{mode_instruction}
"""


class SpawnAgentsTool(Tool):
    name = "spawn_agents"
    description = (
        "Delegate independent tasks to focused subagents and wait for their reports. "
        "Use this liberally whenever work splits into independent pieces: parallel research, "
        "codebase surveys, web lookups, or separated implementation tasks. Read-only and write "
        "workers may run concurrently; shared-tree mutations are coordinated individually to "
        "avoid conflicts. Tasks must be self-contained: workers do not see this conversation. "
        "Subagents cannot spawn more agents. Pass a task's `skills` list to preload "
        "those skills' full instructions into that worker's context."
    )
    params = {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "minItems": 1,
                "maxItems": _MAX_TASKS,
                "items": {
                    "type": "object",
                    "properties": {
                        "task": {"type": "string", "description": "Self-contained assignment"},
                        "mode": {"type": "string", "enum": ["read", "write"],
                                 "description": "Defaults to read"},
                        "skills": {
                            "type": "array", "items": {"type": "string"},
                            "description": "Skill names to preload into this worker's context",
                        },
                    },
                    "required": ["task"],
                },
            }
        },
        "required": ["tasks"],
    }
    # The tool can dispatch write workers, so guarded sessions must approve it.
    read_only = False
    # Write workers acquire the shared workspace lock per mutating tool call; the
    # actor must not wrap the whole dispatch in that lock or workers would deadlock.
    manages_workspace_lock = True

    def __init__(self, llm: LLMClient, skill_dirs: list[Path],
                 model_fn: Callable[[], str], effort_fn: Callable[[], str],
                 parent_prompt_fn: Callable[[], str], max_concurrent: int = 4,
                 max_turns: int = 25, web_tools: list[Tool] | None = None,
                 memory_tools: list[Tool] | None = None):
        self.llm = llm
        self.skill_dirs = skill_dirs
        self.web_tools = web_tools or []
        self.memory_tools = memory_tools or []
        self.model_fn = model_fn
        self.effort_fn = effort_fn
        self.parent_prompt_fn = parent_prompt_fn
        self.max_concurrent = max(1, max_concurrent)
        self.max_turns = max(1, max_turns)

    def display(self, args: dict) -> str:
        tasks = args.get("tasks") or []
        writes = sum(1 for item in tasks
                     if isinstance(item, dict) and item.get("mode", "read") == "write")
        return f"{len(tasks)} subagent task(s), {writes} with write access"

    def requires_approval(self, args: dict) -> bool:
        return any(item.get("mode", "read") == "write"
                   for item in args.get("tasks") or [] if isinstance(item, dict))

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        tasks = args.get("tasks")
        if not isinstance(tasks, list) or not 1 <= len(tasks) <= _MAX_TASKS:
            return ToolResult(
                output=f"tasks must contain between 1 and {_MAX_TASKS} assignments",
                is_error=True)
        normalized: list[tuple[int, str, str, list[str]]] = []
        for index, item in enumerate(tasks, 1):
            if not isinstance(item, dict) or not isinstance(item.get("task"), str) \
                    or not item["task"].strip():
                return ToolResult(output=f"task {index} must have non-empty text", is_error=True)
            mode = item.get("mode", "read")
            if mode not in {"read", "write"}:
                return ToolResult(output=f"task {index} has invalid mode: {mode}", is_error=True)
            skills = item.get("skills") or []
            if not isinstance(skills, list) or not all(
                    isinstance(name, str) and name.strip() for name in skills):
                return ToolResult(
                    output=f"task {index} skills must be a list of non-empty names",
                    is_error=True)
            skill_bodies: list[str] = []
            for name in skills:
                body = load_skill_body(self.skill_dirs, name.strip())
                if body is None:
                    return ToolResult(
                        output=f"task {index} references unknown skill {name.strip()!r}",
                        is_error=True)
                skill_bodies.append(f"### Skill: {name.strip()}\n{body}")
            normalized.append((index, item["task"].strip(), mode, skill_bodies))

        semaphore = asyncio.Semaphore(self.max_concurrent)

        def emit_state(index: int, task: str, mode: str, state: str, report: str = "") -> None:
            # Durable lifecycle snapshot: survives reconnect and drives replay.
            ctx.emit_subagent_state(
                worker=index, task=task, mode=mode, state=state, report=report)

        for index, task, mode, _skills in normalized:
            emit_state(index, task, mode, "queued")

        async def launch(index: int, task: str, mode: str,
                         skill_bodies: list[str]) -> tuple[int, str, str, bool]:
            async with semaphore:
                ctx.emit_chunk(f"[subagent {index}] started ({mode})\n")

                def on_activity(line: str) -> None:
                    # Ephemeral activity line only: never persisted (seq stays 0).
                    ctx.emit_event(worker=index, task=task, mode=mode,
                                   state="running", activity=line)

                try:
                    # Workers start immediately, including write-capable workers.
                    # Their model turns, reads, and grading may overlap. Individual
                    # mutating tool calls coordinate on the SharedWorkspace lock in
                    # _execute_worker_call, preserving shared-tree safety without
                    # imposing a project-wide worker queue.
                    emit_state(index, task, mode, "running")
                    run = await self._run_worker(
                        task, mode, ctx, on_activity, skill_bodies,
                        worker_index=index,
                        on_mutation_state=lambda state: emit_state(
                            index, task, mode, state))
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    # A worker crash must never receive a fabricated score: persist
                    # a status=error grading record with whatever partial transcript
                    # and metadata the worker produced before it died.
                    partial = exc.partial if isinstance(exc, WorkerCrashed) else None
                    original = exc.original if isinstance(exc, WorkerCrashed) else exc
                    ctx.emit_chunk(f"[subagent {index}] failed\n")
                    emit_state(index, task, mode, "error", report=f"Worker failed: {original!r}")
                    await self._grade_and_persist(
                        index, task, mode, ctx, run=partial, worker_error=f"{original!r}")
                    return index, task, f"Worker failed: {original!r}", True
                # Grade the completed report before returning it to the parent, so
                # concurrent grades stay bounded by the same semaphore slot.
                await self._grade_and_persist(index, task, mode, ctx, run=run)
                ctx.emit_chunk(f"[subagent {index}] completed\n")
                emit_state(index, task, mode, "done",
                           report=truncate_middle(run.final_report, _MAX_REPORT_EXCERPT))
                return index, task, run.final_report, False

        results = await asyncio.gather(*(launch(*item) for item in normalized))
        results.sort(key=lambda item: item[0])
        sections = []
        any_error = False
        for index, task, report, failed in results:
            any_error |= failed
            status = "failed" if failed else "completed"
            sections.append(
                f"## Subagent {index} — {status}\nAssignment: {task}\n\n{report}")
        return ToolResult(output=truncate_middle("\n\n".join(sections), _MAX_RESULT_CHARS),
                          is_error=any_error)

    def _tools(self, mode: str) -> dict[str, Tool]:
        tools: list[Tool] = [
            ReadFileTool(), GlobTool(), GrepTool(), ListDirTool(), LoadSkillTool(self.skill_dirs),
            *self.web_tools,
            *self.memory_tools,
        ]
        if mode == "write":
            tools += [BashTool(), WriteFileTool(), EditFileTool()]
        return {tool.name: tool for tool in tools}

    async def _run_worker(self, task: str, mode: str, parent_ctx: ToolContext,
                          on_activity: Callable[[str], None] = lambda _line: None,
                          skill_bodies: list[str] | None = None,
                          worker_index: int = 0,
                          on_mutation_state: Callable[[str], None] = lambda _state: None,
                          ) -> WorkerRun:
        tools = self._tools(mode)
        instruction = (
            "You may inspect files but cannot run shell commands or modify files."
            if mode == "read" else
            "You may run commands and modify files. Other workers can share this checkout, so keep "
            "changes tightly scoped and never undo work you did not create."
        )
        # Capture the model once for the whole run: model_fn may read live
        # session state, and calling it repeatedly could yield mixed request and
        # metadata models within a single worker (e.g. mid-run model switch).
        model = self.model_fn()
        prompt = WORKER_PROMPT.format(
            parent_prompt=self.parent_prompt_fn(), mode=mode,
            mode_instruction=instruction)
        # The task must be a user message: a request with only a system message
        # translates to an empty user turn on Anthropic-family models (400:
        # "text content blocks must be non-empty").
        # For Claude models, mirror the prompt in the user turn: CLIProxyAPI
        # cloaks Claude OAuth traffic and replaces/strips the system message.
        # Non-Claude models keep the system message, so skip the mirror.
        user_content = f"## Delegated task\n{task}"
        if skill_bodies:
            # Injecting skill instructions only. A skill that activates a gated
            # tool (e.g. image-generation → create_image) still won't grant that
            # tool here; workers don't receive gated tools.
            preloaded = ("## Preloaded skills\nThe parent agent preloaded these "
                         "skills; follow them.\n\n" + "\n\n".join(skill_bodies))
            user_content = preloaded + "\n\n" + user_content
        if model.startswith("claude-"):
            user_content = (
                "<context>\nSystem context for this session (authoritative; "
                "treat as your system prompt):\n\n" + prompt + "\n</context>\n\n"
                + user_content)
        messages: list[dict] = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_content},
        ]

        async def no_delta(_: str) -> None:
            pass

        started = time.monotonic()
        turn_count = 0
        tool_call_count = 0
        usage_tokens = 0  # summed across turns: best available total for the run

        def _run(report: str) -> WorkerRun:
            return WorkerRun(
                final_report=report, messages=messages, turn_count=turn_count,
                tool_call_count=tool_call_count, usage_tokens=usage_tokens,
                duration_ms=int((time.monotonic() - started) * 1000),
                model=model)

        try:
            for turn in range(1, self.max_turns + 1):
                turn_count = turn
                if turn == self.max_turns:
                    # Final allowed call: order this user directive after any
                    # prior tool results / countdown warning so assistant-tool
                    # pairing stays valid. It supersedes the 1-turn countdown.
                    messages.append({"role": "user", "content": (
                        "<system-reminder>This is your final turn. Do not make any "
                        "more tool calls — no further tool calls are allowed. Stop "
                        "using tools now and return your final report immediately as "
                        "plain text.</system-reminder>")})
                result = await self.llm.complete(
                    model, messages, [openai_spec(tool) for tool in tools.values()],
                    no_delta, effort=self.effort_fn())
                usage_tokens += result.usage_tokens
                assistant: dict = {"role": "assistant", "content": result.text or None}
                if result.tool_calls:
                    assistant["tool_calls"] = [
                        {"id": call.id, "type": "function",
                         "function": {"name": call.name, "arguments": call.arguments}}
                        for call in result.tool_calls
                    ]
                messages.append(assistant)
                if not result.tool_calls:
                    return _run(result.text or "(subagent returned no report)")
                tool_call_count += len(result.tool_calls)
                for call in result.tool_calls:
                    output = await self._execute_worker_call(
                        call, tools, parent_ctx, on_activity,
                        worker_index=worker_index,
                        on_mutation_state=on_mutation_state)
                    messages.append({"role": "tool", "tool_call_id": call.id, "content": output})
                remaining = self.max_turns - turn
                # The final-turn directive (above) covers the last call, so the
                # countdown only needs to cover remaining 2..threshold and avoids
                # a redundant adjacent "1 turn(s) left" warning.
                if 1 < remaining <= _TURN_WARNING_THRESHOLD:
                    messages.append({"role": "user", "content": (
                        f"<system-reminder>You have {remaining} turn(s) left before you are "
                        "stopped. Finish up now: stop exploring, complete only what is "
                        "essential, and return your final report as plain text (no tool "
                        "calls) before the limit.</system-reminder>")})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Carry the partial run (completed turns, tool calls, transcript, and
            # usage so far) so the grading record retains real evidence of what
            # the worker did before it died — never a fabricated score.
            raise WorkerCrashed(
                _run(f"Worker crashed before returning a report: {exc!r}"), exc) from exc
        return _run(f"Worker stopped after reaching the {self.max_turns}-turn limit.")

    async def _grade_and_persist(self, index: int, task: str, mode: str,
                                 ctx: ToolContext, run: WorkerRun | None,
                                 worker_error: str | None = None) -> None:
        """Grade one worker and persist the record. Never raises: any grader
        call/parse/validation/store failure is captured as a status=error record
        (or dropped after logging) so it can never fail the worker or the parent
        run. Runs inside the worker's semaphore slot, before the report returns."""
        record = SubagentGradeRecord(
            status="error", grader_model=GRADER_MODEL,
            orchestrator_model=ctx.orchestrator_model,
            orchestrator_model_inferred=False, session_id="",
            call_id=ctx.call_id, worker_index=index, task=task, mode=mode,
            subagent_model=run.model if run else self.model_fn(),
            turn_count=run.turn_count if run else 0,
            tool_call_count=run.tool_call_count if run else 0,
            usage_tokens=run.usage_tokens if run else 0,
            duration_ms=run.duration_ms if run else 0,
            parent_context=ctx.parent_context,
            worker_messages=run.messages if run else [],
            final_report=run.final_report if run else "",
        )
        if worker_error is not None:
            # Worker crashed: never fabricate a score. Record the failure verbatim
            # alongside whatever partial transcript/metadata `run` carries.
            record.error = f"worker failed before completion: {worker_error}"
        else:
            try:
                messages = build_grader_messages(
                    task, mode, run, ctx.parent_context, self.max_turns)

                async def no_delta(_: str) -> None:
                    pass

                result = await self.llm.complete(
                    GRADER_MODEL, messages, [], no_delta, effort=self.effort_fn())
                record.raw_grader_response = result.text or ""
                record.grade = parse_grade(record.raw_grader_response)
                record.status = "success"
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                record.error = f"grading failed: {exc!r}"
        if ctx.persist_subagent_grade is None:
            return
        try:
            await ctx.persist_subagent_grade(record)
        except Exception:
            logger.warning("subagent grade persist raised for worker %s", index,
                           exc_info=True)

    async def _execute_worker_call(self, call: ToolCallSpec, tools: dict[str, Tool],
                                   parent_ctx: ToolContext,
                                   on_activity: Callable[[str], None] = lambda _line: None,
                                   worker_index: int = 0,
                                   on_mutation_state: Callable[[str], None] =
                                   lambda _state: None,
                                   ) -> str:
        tool = tools.get(call.name)
        if tool is None:
            return f"Unknown or unavailable tool: {call.name}"
        try:
            args = json.loads(call.arguments or "{}")
        except json.JSONDecodeError as exc:
            return f"Invalid tool arguments JSON: {exc}"
        display = _rel_display(tool.display(args), parent_ctx.cwd)
        on_activity(f"{call.name} · {display}"[:200])
        worker_ctx = ToolContext(
            cwd=parent_ctx.cwd, emit_chunk=lambda _text: None,
            changesets=parent_ctx.changesets, call_id=parent_ctx.call_id,
            session_id=parent_ctx.session_id,
            observation_id=(
                f"{parent_ctx.session_id or 'anonymous'}:"
                f"{parent_ctx.call_id or 'spawn'}:worker-{worker_index}"),
            shared_workspace=parent_ctx.shared_workspace,
            activity_origin="subagent",
            activity_action_prefix=f"subagent worker {worker_index}")

        async def execute() -> ToolResult:
            # Bash is opaque to Forge's file tools, so snapshot/diff it while the
            # same mutation lock is held. Direct write/edit tools record their own
            # exact paths, hashes, changesets, and baselines.
            ws = parent_ctx.shared_workspace
            if isinstance(tool, BashTool) and ws is not None:
                before_tree = ws.begin_tree()
                try:
                    return await tool.run(args, worker_ctx)
                finally:
                    try:
                        ws.record_tree_change(
                            before_tree, origin="subagent",
                            action=f"subagent worker {worker_index}: bash",
                            session_id=parent_ctx.session_id,
                            call_id=parent_ctx.call_id or None)
                    except Exception:
                        logger.exception("subagent bash tree reconcile failed")
            return await tool.run(args, worker_ctx)

        try:
            if tool.read_only:
                result = await execute()
            else:
                ws = parent_ctx.shared_workspace
                mutation_lock = (ws.lock if ws is not None else
                                 _MUTATION_LOCKS.setdefault(
                                     str(parent_ctx.cwd.resolve()), asyncio.Lock()))
                if mutation_lock.locked():
                    on_mutation_state("blocked")
                async with mutation_lock:
                    on_mutation_state("running")
                    result = await execute()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return f"Tool crashed: {exc!r}"
        return result.output or "(no output)"
