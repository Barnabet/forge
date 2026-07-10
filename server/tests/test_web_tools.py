import json

import httpx

from forge.store.config import ForgeConfig
from forge.tools.base import ToolContext
from forge.tools.registry import default_tools, web_tools_from_config
from forge.tools.web import FetchPageTool, WebSearchTool


def ctx(tmp_path):
    return ToolContext(cwd=tmp_path)


def serper_transport(payload, status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://google.serper.dev/search"
        assert request.headers["X-API-KEY"] == "sk-serper"
        return httpx.Response(status, json=payload)
    return httpx.MockTransport(handler)


async def test_web_search_formats_results(tmp_path):
    payload = {
        "answerBox": {"answer": "42"},
        "organic": [
            {"title": "Deep Thought", "link": "https://ex.com/dt", "snippet": "The answer."},
            {"title": "No snippet", "link": "https://ex.com/ns"},
        ],
    }
    tool = WebSearchTool("sk-serper", transport=serper_transport(payload))
    res = await tool.run({"query": "meaning of life"}, ctx(tmp_path))
    assert not res.is_error
    assert "Answer: 42" in res.output
    assert "Deep Thought\n  https://ex.com/dt\n  The answer." in res.output
    assert "No snippet" in res.output


async def test_web_search_respects_num_and_query(tmp_path):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"organic": []})

    tool = WebSearchTool("sk-serper", transport=httpx.MockTransport(handler))
    res = await tool.run({"query": "q", "num": 3}, ctx(tmp_path))
    assert seen == {"q": "q", "num": 3}
    assert res.output == "No results."


async def test_web_search_api_error_is_tool_error(tmp_path):
    tool = WebSearchTool("sk-serper", transport=serper_transport({"message": "bad key"}, 403))
    res = await tool.run({"query": "q"}, ctx(tmp_path))
    assert res.is_error and "403" in res.output


async def test_web_search_network_error_is_tool_error(tmp_path):
    def handler(request):
        raise httpx.ConnectError("boom")
    tool = WebSearchTool("sk-serper", transport=httpx.MockTransport(handler))
    res = await tool.run({"query": "q"}, ctx(tmp_path))
    assert res.is_error and "Search request failed" in res.output


def firecrawl_transport(payload, status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.firecrawl.dev/v1/scrape"
        assert request.headers["Authorization"] == "Bearer sk-fc"
        return httpx.Response(status, json=payload)
    return httpx.MockTransport(handler)


async def test_fetch_page_returns_markdown(tmp_path):
    tool = FetchPageTool(
        "sk-fc", transport=firecrawl_transport({"data": {"markdown": "# Hello\n\nWorld"}}))
    res = await tool.run({"url": "https://ex.com"}, ctx(tmp_path))
    assert not res.is_error and res.output == "# Hello\n\nWorld"


async def test_fetch_page_rejects_non_http_url(tmp_path):
    tool = FetchPageTool("sk-fc")
    res = await tool.run({"url": "file:///etc/passwd"}, ctx(tmp_path))
    assert res.is_error and "Not an http(s) URL" in res.output


async def test_fetch_page_empty_content_is_error(tmp_path):
    tool = FetchPageTool("sk-fc", transport=firecrawl_transport({"data": {}}))
    res = await tool.run({"url": "https://ex.com"}, ctx(tmp_path))
    assert res.is_error and "no content" in res.output


async def test_fetch_page_api_error_is_tool_error(tmp_path):
    tool = FetchPageTool("sk-fc", transport=firecrawl_transport({"error": "nope"}, 402))
    res = await tool.run({"url": "https://ex.com"}, ctx(tmp_path))
    assert res.is_error and "402" in res.output


def test_web_tools_gated_on_config_keys():
    assert web_tools_from_config() == []
    names = [t.name for t in web_tools_from_config("sk-s", "sk-f")]
    assert names == ["web_search", "fetch_page"]
    assert [t.name for t in web_tools_from_config(serper_api_key="sk-s")] == ["web_search"]


def test_default_tools_include_web_tools_and_are_read_only():
    cfg = ForgeConfig(serper_api_key="sk-s", firecrawl_api_key="sk-f")
    tools = default_tools([], web_tools=web_tools_from_config(
        cfg.serper_api_key, cfg.firecrawl_api_key))
    assert "web_search" in tools and "fetch_page" in tools
    assert tools["web_search"].read_only and tools["fetch_page"].read_only
    assert not tools["web_search"].requires_approval({"query": "q"})
