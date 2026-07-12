from __future__ import annotations

import asyncio

from forge.llm.base import CompletionResult, OnTextDelta, OnToolCallStart


class FakeLLM:
    """Scripted LLM for deterministic end-to-end engine tests."""

    # Stable marker that opens every subagent grader prompt. Kept as a literal
    # (not imported) so fake.py has no dependency on the tools package.
    _GRADER_MARKER = "You are an impartial evaluator grading the work of an AI subagent."

    def __init__(self, script: list[CompletionResult | Exception], delay: float = 0.0,
                 memory_script: list[CompletionResult | Exception] | None = None,
                 grader_script: list[CompletionResult | Exception] | None = None):
        self.script = list(script)
        self.memory_script = list(memory_script or [])
        self.grader_script = list(grader_script or [])
        self.delay = delay
        self.calls: list[list[dict]] = []
        self.efforts: list[str] = []
        self.memory_calls: list[list[dict]] = []
        self.grader_calls: list[list[dict]] = []

    async def complete(
        self, model: str, messages: list[dict], tools: list[dict],
        on_text_delta: OnTextDelta, effort: str = "default",
        on_tool_start: OnToolCallStart | None = None,
    ) -> CompletionResult:
        # Subagent grader calls route to their own script so they never steal
        # results from the main conversation or memory scripts. Keyed on a stable
        # prompt marker in the (single, user-role) grader message.
        if messages and self._GRADER_MARKER in str(messages[0].get("content", "")):
            self.grader_calls.append(messages)
            if not self.grader_script:
                # Backward compat: tests that don't supply a grader_script still
                # exercise subagents unchanged. Return an unparseable response so
                # the caller persists a status=error record and ignores it rather
                # than stealing a real script item.
                return CompletionResult(text="(no grader script configured)")
            item = self.grader_script.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        # Background memory-agent dreams pull from their own script so they never
        # steal results from the main conversation script.
        if messages and "You are the memory agent" in str(messages[0].get("content", "")):
            self.memory_calls.append(messages)
            if not self.memory_script:
                return CompletionResult(text="no change")
            item = self.memory_script.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        self.calls.append(messages)
        self.efforts.append(effort)
        if self.delay:
            await asyncio.sleep(self.delay)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if item.text:
            await on_text_delta(item.text)
        if on_tool_start:  # mimic the stream announcing each call up front
            for call in item.tool_calls:
                await on_tool_start(call.id, call.name)
        return item

    async def healthy(self) -> bool:
        return True
