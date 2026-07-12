from __future__ import annotations

import base64

import httpx

from forge.tools.base import Tool, ToolContext, ToolResult

_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp",
        "image/svg+xml": "svg"}


class CreateImageTool(Tool):
    name = "create_image"
    description = (
        "Generate an image from a text prompt and save it to a file. Requires the "
        "image-generation skill (load it first to master the prompt). Writes the "
        "image to `path` and shows it inline.")
    params = {"type": "object", "properties": {
        "prompt": {"type": "string", "description": "The image generation prompt"},
        "path": {"type": "string",
                 "description": "Output file path (extension set from the result format)"},
        "size": {"type": "string",
                 "description": "Pixel size, e.g. 1024x1024, 1536x1024, 1024x1536, or auto"},
        "quality": {"type": "string", "enum": ["auto", "low", "medium", "high"]},
        "background": {"type": "string", "enum": ["auto", "opaque", "transparent"]},
        "n": {"type": "integer", "description": "Number of images (1-10, default 1)"},
    }, "required": ["prompt", "path"]}

    def __init__(self, api_key: str, model: str,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.api_key = api_key
        self.model = model
        self._transport = transport

    def display(self, args: dict) -> str:
        return args.get("path", self.name)

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        prompt = args.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            return ToolResult(output="prompt must be a non-empty string", is_error=True)
        n = max(1, min(int(args.get("n") or 1), 10))
        body: dict = {"model": self.model, "prompt": prompt, "n": n}
        for key in ("size", "quality", "background"):
            if args.get(key):
                body[key] = args[key]
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=180) as client:
                r = await client.post(
                    "https://openrouter.ai/api/v1/images",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=body)
        except httpx.HTTPError as e:
            return ToolResult(output=f"Image request failed: {e!r}", is_error=True)
        if r.status_code != 200:
            return ToolResult(
                output=f"OpenRouter error {r.status_code}: {r.text[:500]}", is_error=True)
        data = r.json().get("data") or []
        if not data:
            return ToolResult(output="No image returned by the provider.", is_error=True)

        out_path = ctx.resolve(args["path"])
        stem = out_path.with_suffix("")
        saved: list[str] = []
        images: list[str] = []
        for i, item in enumerate(data):
            b64 = item.get("b64_json")
            if not b64:
                continue
            media = item.get("media_type") or "image/png"
            ext = _EXT.get(media, "png")
            p = out_path if len(data) == 1 else stem.with_name(f"{stem.name}-{i + 1}.{ext}")
            if len(data) == 1 and not out_path.suffix:
                p = out_path.with_suffix(f".{ext}")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(base64.b64decode(b64))
            saved.append(str(p))
            images.append(f"data:{media};base64,{b64}")
        if not saved:
            return ToolResult(output="Provider response had no image data.", is_error=True)
        summary = "Generated " + ", ".join(saved)
        return ToolResult(output=summary, images=images)
