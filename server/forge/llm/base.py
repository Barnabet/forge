from __future__ import annotations

from typing import Awaitable, Callable, Protocol

from pydantic import BaseModel

from forge.engine.events import ToolCallSpec

OnTextDelta = Callable[[str], Awaitable[None]]
OnToolCallStart = Callable[[str, str], Awaitable[None]]  # (call_id, tool_name)


class CompletionResult(BaseModel):
    text: str
    tool_calls: list[ToolCallSpec] = []
    usage_tokens: int = 0


class LLMError(Exception):
    """Raised when the model call fails after retries."""


class LLMClient(Protocol):
    async def complete(
        self, model: str, messages: list[dict], tools: list[dict],
        on_text_delta: OnTextDelta, effort: str = "default",
        on_tool_start: OnToolCallStart | None = None,
    ) -> CompletionResult: ...

    async def healthy(self) -> bool: ...
