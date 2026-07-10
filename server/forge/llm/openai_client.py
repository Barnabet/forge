from __future__ import annotations

import asyncio

from openai import (
    APIConnectionError, AsyncOpenAI, InternalServerError, OpenAIError,
    RateLimitError,
)

from forge.engine.events import ToolCallSpec
from forge.llm.base import CompletionResult, LLMError, OnTextDelta, OnToolCallStart

RETRYABLE = (APIConnectionError, RateLimitError, InternalServerError)


class OpenAILLM:
    def __init__(self, base_url: str, api_key: str,
                 retry_delays: tuple[float, ...] = (1, 2, 4)):
        self.client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        self.retry_delays = retry_delays

    async def complete(self, model: str, messages: list[dict], tools: list[dict],
                       on_text_delta: OnTextDelta, effort: str = "default",
                       on_tool_start: OnToolCallStart | None = None,
                       ) -> CompletionResult:
        last: Exception | None = None
        for delay in (0, *self.retry_delays):
            if delay:
                await asyncio.sleep(delay)
            try:
                return await self._stream_once(
                    model, messages, tools, on_text_delta, effort, on_tool_start)
            except RETRYABLE as e:
                last = e
            except OpenAIError as e:  # non-retryable (auth, bad model) → fail fast
                raise LLMError(f"LLM call failed: {e}") from e
        raise LLMError(f"LLM call failed after retries: {last}")

    async def _stream_once(self, model, messages, tools, on_text_delta,
                           effort="default", on_tool_start=None):
        kwargs: dict = {"model": model, "messages": messages, "stream": True,
                        "stream_options": {"include_usage": True}}
        if tools:
            kwargs["tools"] = tools
        if effort != "default":
            kwargs["reasoning_effort"] = effort
        stream = await self.client.chat.completions.create(**kwargs)

        text_parts: list[str] = []
        calls: dict[int, dict] = {}
        announced: set[int] = set()
        usage = 0
        async for chunk in stream:
            if getattr(chunk, "usage", None):
                usage = chunk.usage.total_tokens
            if not chunk.choices:
                continue
            d = chunk.choices[0].delta
            if d is None:
                continue
            if d.content:
                text_parts.append(d.content)
                await on_text_delta(d.content)
            for tc in d.tool_calls or []:
                c = calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                if tc.id:
                    c["id"] = tc.id
                if tc.function and tc.function.name:
                    c["name"] += tc.function.name
                if tc.function and tc.function.arguments:
                    c["arguments"] += tc.function.arguments
                # Announce each call the moment id+name are known — long
                # argument streams (big edits) follow for seconds after.
                if on_tool_start and tc.index not in announced \
                        and c["id"] and c["name"]:
                    announced.add(tc.index)
                    await on_tool_start(c["id"], c["name"])
        return CompletionResult(
            text="".join(text_parts),
            tool_calls=[ToolCallSpec(**calls[i]) for i in sorted(calls)],
            usage_tokens=usage,
        )

    async def healthy(self) -> bool:
        try:
            await self.client.models.list()
            return True
        except Exception:
            return False
