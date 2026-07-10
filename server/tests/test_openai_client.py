from types import SimpleNamespace as NS

import pytest
from openai import APIConnectionError

from forge.llm.base import LLMError
from forge.llm.openai_client import OpenAILLM


def chunk(content=None, tool_calls=None, usage=None):
    choice = NS(delta=NS(content=content, tool_calls=tool_calls))
    return NS(choices=[choice] if content or tool_calls else [], usage=usage)


def tc(index, id=None, name=None, arguments=None):
    return NS(index=index, id=id,
              function=NS(name=name, arguments=arguments))


class FakeStream:
    def __init__(self, chunks): self._chunks = list(chunks)
    def __aiter__(self): return self
    async def __anext__(self):
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)


def make_llm(responses):
    """responses: list of chunk-lists or exceptions, one per create() call."""
    llm = OpenAILLM("http://x/v1", "k", retry_delays=(0,))
    calls = []

    async def create(**kwargs):
        calls.append(kwargs)
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return FakeStream(item)

    llm.client = NS(chat=NS(completions=NS(create=create)))
    return llm, calls


async def test_assembles_text_and_tool_calls():
    llm, calls = make_llm([[
        chunk(content="Hel"), chunk(content="lo"),
        chunk(tool_calls=[tc(0, id="c1", name="bash", arguments='{"comm')]),
        chunk(tool_calls=[tc(0, arguments='and": "ls"}')]),
        chunk(usage=NS(total_tokens=42)),
    ]])
    deltas = []

    async def on_delta(t): deltas.append(t)

    r = await llm.complete("m", [{"role": "user", "content": "hi"}],
                           [{"type": "function"}], on_delta)
    assert r.text == "Hello" and deltas == ["Hel", "lo"]
    assert r.tool_calls[0].id == "c1"
    assert r.tool_calls[0].arguments == '{"command": "ls"}'
    assert r.usage_tokens == 42
    assert calls[0]["stream"] is True and "tools" in calls[0]


async def test_retries_then_raises_llm_error():
    conn_err = APIConnectionError(request=NS())
    llm, calls = make_llm([conn_err, conn_err])

    async def on_delta(t): ...

    with pytest.raises(LLMError):
        await llm.complete("m", [], [], on_delta)
    assert len(calls) == 2  # first try + one retry (retry_delays=(0,))
