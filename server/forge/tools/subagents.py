from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path

from forge.engine.events import ToolCallSpec
from forge.llm.base import LLMClient
from forge.tools.base import Tool, ToolContext, ToolResult, openai_spec, truncate_middle
from forge.tools.bash import BashTool
from forge.tools.files_read import ReadFileTool
from forge.tools.files_write import EditFileTool, WriteFileTool
from forge.tools.search import GlobTool, GrepTool, ListDirTool
from forge.tools.skills_tool import LoadSkillTool

_MAX_TASKS = 4
_MAX_RESULT_CHARS = 30_000
_WRITE_LOCKS: dict[str, asyncio.Lock] = {}

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
        "Read-only tasks run concurrently. Write tasks may edit the shared working tree but "
        "are serialized to avoid conflicts. Subagents cannot spawn more agents."
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
                    },
                    "required": ["task"],
                },
            }
        },
        "required": ["tasks"],
    }
    # The tool can dispatch write workers, so guarded sessions must approve it.
    read_only = False

    def __init__(self, llm: LLMClient, skill_dirs: list[Path],
                 model_fn: Callable[[], str], effort_fn: Callable[[], str],
                 parent_prompt_fn: Callable[[], str], max_concurrent: int = 4,
                 max_turns: int = 12, web_tools: list[Tool] | None = None):
        self.llm = llm
        self.skill_dirs = skill_dirs
        self.web_tools = web_tools or []
        self.model_fn = model_fn
        self.effort_fn = effort_fn
        self.parent_prompt_fn = parent_prompt_fn
        self.max_concurrent = max(1, max_concurrent)
        self.max_turns = max(1, max_turns)

    def display(self, args: dict) -> str:
        tasks = args.get("tasks") or []
        writes = sum(1 for item in tasks if item.get("mode", "read") == "write")
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
        normalized: list[tuple[int, str, str]] = []
        for index, item in enumerate(tasks, 1):
            if not isinstance(item, dict) or not isinstance(item.get("task"), str) \
                    or not item["task"].strip():
                return ToolResult(output=f"task {index} must have non-empty text", is_error=True)
            mode = item.get("mode", "read")
            if mode not in {"read", "write"}:
                return ToolResult(output=f"task {index} has invalid mode: {mode}", is_error=True)
            normalized.append((index, item["task"].strip(), mode))

        semaphore = asyncio.Semaphore(self.max_concurrent)

        async def launch(index: int, task: str, mode: str) -> tuple[int, str, str, bool]:
            async with semaphore:
                ctx.emit_chunk(f"[subagent {index}] started ({mode})\n")
                try:
                    if mode == "write":
                        write_lock = _WRITE_LOCKS.setdefault(str(ctx.cwd.resolve()), asyncio.Lock())
                        async with write_lock:
                            report = await self._run_worker(task, mode, ctx)
                    else:
                        report = await self._run_worker(task, mode, ctx)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    ctx.emit_chunk(f"[subagent {index}] failed\n")
                    return index, task, f"Worker failed: {exc!r}", True
                ctx.emit_chunk(f"[subagent {index}] completed\n")
                return index, task, report, False

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
        ]
        if mode == "write":
            tools += [BashTool(), WriteFileTool(), EditFileTool()]
        return {tool.name: tool for tool in tools}

    async def _run_worker(self, task: str, mode: str, parent_ctx: ToolContext) -> str:
        tools = self._tools(mode)
        instruction = (
            "You may inspect files but cannot run shell commands or modify files."
            if mode == "read" else
            "You may run commands and modify files. Other workers can share this checkout, so keep "
            "changes tightly scoped and never undo work you did not create."
        )
        prompt = WORKER_PROMPT.format(
            parent_prompt=self.parent_prompt_fn(), mode=mode,
            mode_instruction=instruction)
        # The task must be a user message: a request with only a system message
        # translates to an empty user turn on Anthropic-family models (400:
        # "text content blocks must be non-empty").
        messages: list[dict] = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"## Delegated task\n{task}"},
        ]

        async def no_delta(_: str) -> None:
            pass

        for _ in range(self.max_turns):
            result = await self.llm.complete(
                self.model_fn(), messages, [openai_spec(tool) for tool in tools.values()],
                no_delta, effort=self.effort_fn())
            assistant: dict = {"role": "assistant", "content": result.text or None}
            if result.tool_calls:
                assistant["tool_calls"] = [
                    {"id": call.id, "type": "function",
                     "function": {"name": call.name, "arguments": call.arguments}}
                    for call in result.tool_calls
                ]
            messages.append(assistant)
            if not result.tool_calls:
                return result.text or "(subagent returned no report)"
            for call in result.tool_calls:
                output = await self._execute_worker_call(call, tools, parent_ctx)
                messages.append({"role": "tool", "tool_call_id": call.id, "content": output})
        return f"Worker stopped after reaching the {self.max_turns}-turn limit."

    async def _execute_worker_call(self, call: ToolCallSpec, tools: dict[str, Tool],
                                   parent_ctx: ToolContext) -> str:
        tool = tools.get(call.name)
        if tool is None:
            return f"Unknown or unavailable tool: {call.name}"
        try:
            args = json.loads(call.arguments or "{}")
        except json.JSONDecodeError as exc:
            return f"Invalid tool arguments JSON: {exc}"
        worker_ctx = ToolContext(
            cwd=parent_ctx.cwd, emit_chunk=lambda _text: None,
            changesets=parent_ctx.changesets)
        try:
            result = await tool.run(args, worker_ctx)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return f"Tool crashed: {exc!r}"
        return result.output or "(no output)"
