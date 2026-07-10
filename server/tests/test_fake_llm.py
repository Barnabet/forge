import pytest

from forge.engine.events import ToolCallSpec
from forge.llm.base import CompletionResult, LLMError
from forge.llm.fake import FakeLLM


async def test_fake_llm_pops_script_and_streams():
    fake = FakeLLM([CompletionResult(text="hello", tool_calls=[], usage_tokens=10)])
    deltas: list[str] = []

    async def on_delta(t: str):
        deltas.append(t)

    r = await fake.complete("m", [{"role": "user", "content": "hi"}], [], on_delta)
    assert r.text == "hello" and deltas == ["hello"]
    assert fake.calls[0][0]["content"] == "hi"
    assert await fake.healthy()


async def test_fake_llm_raises_scripted_errors():
    fake = FakeLLM([LLMError("boom")])

    async def on_delta(t: str): ...

    with pytest.raises(LLMError):
        await fake.complete("m", [], [], on_delta)


def test_completion_result_holds_tool_calls():
    r = CompletionResult(
        text="", tool_calls=[ToolCallSpec(id="c1", name="bash", arguments="{}")],
        usage_tokens=5)
    assert r.tool_calls[0].name == "bash"
