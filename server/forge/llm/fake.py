from __future__ import annotations

import asyncio

from forge.llm.base import CompletionResult, OnTextDelta, OnToolCallStart


class FakeLLM:
    """Scripted LLM for deterministic end-to-end engine tests."""

    def __init__(self, script: list[CompletionResult | Exception], delay: float = 0.0):
        self.script = list(script)
        self.delay = delay
        self.calls: list[list[dict]] = []
        self.efforts: list[str] = []

    async def complete(
        self, model: str, messages: list[dict], tools: list[dict],
        on_text_delta: OnTextDelta, effort: str = "default",
        on_tool_start: OnToolCallStart | None = None,
    ) -> CompletionResult:
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
