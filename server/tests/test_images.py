import base64
import json

import httpx

from forge.store.config import ForgeConfig
from forge.tools.base import ToolContext
from forge.tools.images import CreateImageTool
from forge.tools.registry import default_tools, image_tool_from_config

PNG_B64 = base64.b64encode(b"\x89PNG\r\n\x1a\nfakepng").decode()


def ctx(tmp_path):
    return ToolContext(cwd=tmp_path)


def images_transport(payload, status=200, seen=None):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://openrouter.ai/api/v1/images"
        assert request.headers["Authorization"] == "Bearer sk-or"
        if seen is not None:
            seen.update(json.loads(request.content))
        return httpx.Response(status, json=payload)
    return httpx.MockTransport(handler)


async def test_create_image_writes_file_and_returns_inline(tmp_path):
    payload = {"data": [{"b64_json": PNG_B64, "media_type": "image/png"}]}
    tool = CreateImageTool("sk-or", "openai/gpt-image-2",
                           transport=images_transport(payload))
    res = await tool.run({"prompt": "an anvil logo", "path": "assets/logo.png"},
                         ctx(tmp_path))
    assert not res.is_error
    saved = tmp_path / "assets" / "logo.png"
    assert saved.read_bytes() == base64.b64decode(PNG_B64)
    assert len(res.images) == 1 and res.images[0].startswith("data:image/png;base64,")
    assert "assets/logo.png" in res.output or str(saved) in res.output


async def test_create_image_forwards_params(tmp_path):
    seen: dict = {}
    payload = {"data": [{"b64_json": PNG_B64, "media_type": "image/png"}]}
    tool = CreateImageTool("sk-or", "openai/gpt-image-2",
                           transport=images_transport(payload, seen=seen))
    await tool.run({"prompt": "x", "path": "o.png", "size": "1024x1024",
                    "quality": "high", "background": "transparent", "n": 2},
                   ctx(tmp_path))
    assert seen["model"] == "openai/gpt-image-2"
    assert seen["size"] == "1024x1024" and seen["quality"] == "high"
    assert seen["background"] == "transparent" and seen["n"] == 2


async def test_create_image_multiple_images_numbered(tmp_path):
    payload = {"data": [
        {"b64_json": PNG_B64, "media_type": "image/png"},
        {"b64_json": PNG_B64, "media_type": "image/png"}]}
    tool = CreateImageTool("sk-or", "openai/gpt-image-2",
                           transport=images_transport(payload))
    res = await tool.run({"prompt": "x", "path": "mark.png", "n": 2}, ctx(tmp_path))
    assert not res.is_error
    assert (tmp_path / "mark-1.png").is_file() and (tmp_path / "mark-2.png").is_file()
    assert len(res.images) == 2


async def test_create_image_empty_prompt_is_error(tmp_path):
    tool = CreateImageTool("sk-or", "openai/gpt-image-2")
    res = await tool.run({"prompt": "  ", "path": "o.png"}, ctx(tmp_path))
    assert res.is_error and "prompt" in res.output


async def test_create_image_api_error_is_tool_error(tmp_path):
    tool = CreateImageTool("sk-or", "openai/gpt-image-2",
                           transport=images_transport({"error": "nope"}, 402))
    res = await tool.run({"prompt": "x", "path": "o.png"}, ctx(tmp_path))
    assert res.is_error and "402" in res.output


async def test_create_image_no_data_is_error(tmp_path):
    tool = CreateImageTool("sk-or", "openai/gpt-image-2",
                           transport=images_transport({"data": []}))
    res = await tool.run({"prompt": "x", "path": "o.png"}, ctx(tmp_path))
    assert res.is_error


def test_image_tool_gated_on_openrouter_key():
    assert image_tool_from_config() is None
    tool = image_tool_from_config("sk-or", "openai/gpt-image-2")
    assert tool is not None and tool.name == "create_image"


def test_default_tools_include_image_tool_when_configured():
    cfg = ForgeConfig(openrouter_api_key="sk-or")
    tools = default_tools([], image_tool=image_tool_from_config(
        cfg.openrouter_api_key, cfg.image_model))
    assert "create_image" in tools
    assert not tools["create_image"].read_only
