from __future__ import annotations

import asyncio

from forge.llm.base import CompletionResult, OnTextDelta


class FakeLLM:
    """Scripted LLM for deterministic end-to-end engine tests."""

    def __init__(self, script: list[CompletionResult | Exception], delay: float = 0.0):
        self.script = list(script)
        self.delay = delay
        self.calls: list[list[dict]] = []

    async def complete(
        self, model: str, messages: list[dict], tools: list[dict],
        on_text_delta: OnTextDelta,
    ) -> CompletionResult:
        self.calls.append(messages)
        if self.delay:
            await asyncio.sleep(self.delay)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if item.text:
            await on_text_delta(item.text)
        return item

    async def healthy(self) -> bool:
        return True
