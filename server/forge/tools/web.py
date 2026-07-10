from __future__ import annotations

import httpx

from forge.tools.base import Tool, ToolContext, ToolResult, truncate_middle


class WebSearchTool(Tool):
    name = "web_search"
    description = ("Search the web (Google results via Serper). Returns titles, "
                   "URLs, and snippets. Use fetch_page to read a result in full.")
    params = {"type": "object", "properties": {
        "query": {"type": "string"},
        "num": {"type": "integer", "description": "max results (default 8)"}},
        "required": ["query"]}
    read_only = True

    def __init__(self, api_key: str, transport: httpx.AsyncBaseTransport | None = None):
        self.api_key = api_key
        self._transport = transport

    def display(self, args: dict) -> str:
        return args.get("query", self.name)

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        num = min(int(args.get("num") or 8), 20)
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=20) as client:
                r = await client.post(
                    "https://google.serper.dev/search",
                    headers={"X-API-KEY": self.api_key},
                    json={"q": args["query"], "num": num})
        except httpx.HTTPError as e:
            return ToolResult(output=f"Search request failed: {e!r}", is_error=True)
        if r.status_code != 200:
            return ToolResult(
                output=f"Serper error {r.status_code}: {r.text[:500]}", is_error=True)
        data = r.json()
        lines: list[str] = []
        box = data.get("answerBox") or {}
        if box.get("answer") or box.get("snippet"):
            lines.append(f"Answer: {box.get('answer') or box.get('snippet')}")
            lines.append("")
        for hit in data.get("organic", [])[:num]:
            lines.append(f"{hit.get('title', '(untitled)')}\n  {hit.get('link', '')}")
            if hit.get("snippet"):
                lines.append(f"  {hit['snippet']}")
        return ToolResult(output="\n".join(lines) or "No results.")


class FetchPageTool(Tool):
    name = "fetch_page"
    description = ("Fetch a web page and return its main content as markdown "
                   "(rendered via Firecrawl; handles JS-heavy pages).")
    params = {"type": "object", "properties": {"url": {"type": "string"}},
              "required": ["url"]}
    read_only = True

    def __init__(self, api_key: str, transport: httpx.AsyncBaseTransport | None = None):
        self.api_key = api_key
        self._transport = transport

    def display(self, args: dict) -> str:
        return args.get("url", self.name)

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        url = args["url"]
        if not url.startswith(("http://", "https://")):
            return ToolResult(output=f"Not an http(s) URL: {url}", is_error=True)
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=60) as client:
                r = await client.post(
                    "https://api.firecrawl.dev/v1/scrape",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={"url": url, "formats": ["markdown"]})
        except httpx.HTTPError as e:
            return ToolResult(output=f"Fetch request failed: {e!r}", is_error=True)
        if r.status_code != 200:
            return ToolResult(
                output=f"Firecrawl error {r.status_code}: {r.text[:500]}", is_error=True)
        data = r.json().get("data") or {}
        markdown = data.get("markdown") or ""
        if not markdown:
            return ToolResult(output="Page fetched but no content extracted.", is_error=True)
        return ToolResult(output=truncate_middle(markdown))
